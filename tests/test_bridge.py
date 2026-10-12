import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from unittest.mock import Mock, patch

from runner_bridge.api import API, APIError, BridgeError, app_jwt
from runner_bridge.config import Config, identity, pipeline, pullfrog_workflow, step
from runner_bridge.plugin import dispatch, eligible, plugin_main, supervise, waiting_jobs
from runner_bridge.setup import Installer, State, manifest, patch_workflow, register_app


def config():
    return Config(repository="example/project", queue="linux", plugin="example/github-runner#" + "a" * 40,
                  app_id="12", installation_id="34").validate()


def job(status="queued", attempt=1):
    return {"id": 42, "run_id": 7, "run_attempt": attempt, "status": status,
            "labels": ["buildkite-pullfrog", f"pullfrog-7-{attempt}"]}


def run():
    return {"id": 7, "run_attempt": 1, "path": ".github/workflows/pullfrog.yml", "event": "workflow_dispatch",
            "head_branch": "main", "repository": {"full_name": "example/project"}}


class GithubFixture:
    def __init__(self, jobs=None):
        self.jobs = jobs or [job()]
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if "/actions/jobs/" in path:
            return copy.deepcopy(self.jobs[0])
        if "/actions/runs/" in path:
            return run()
        if path.endswith("registration-token"):
            return {"token": "fixture-registration-token"}
        raise AssertionError(path)

    def pages(self, path, key=None):
        self.calls.append(("PAGES", path, key))
        if path.endswith("/runners"):
            return iter([])
        if "/jobs?" in path:
            return iter(copy.deepcopy(self.jobs))
        return iter([run()] if "status=in_progress" in path else [])


class SchedulingTests(unittest.TestCase):
    @patch("runner_bridge.plugin.time.sleep")
    @patch("runner_bridge.plugin.subprocess.run")
    @patch("runner_bridge.plugin.subprocess.Popen")
    @patch("runner_bridge.plugin.runner_image", return_value="fixture-image")
    @patch("runner_bridge.plugin.live_job")
    def test_transient_monitor_error_does_not_abort_review(self, live, image, popen, docker, sleep):
        live.side_effect = [(job(), run()), (job(), run()), BridgeError("temporary outage"),
                            (dict(job("completed"), conclusion="success"), run())]
        popen.return_value.poll.return_value = None
        docker.return_value.returncode = 0
        self.assertEqual(supervise(config(), GithubFixture(), 42), 0)
        self.assertEqual(live.call_count, 4)

    @patch("runner_bridge.plugin.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 30))
    @patch("runner_bridge.plugin.subprocess.Popen")
    @patch("runner_bridge.plugin.runner_image", return_value="fixture-image")
    @patch("runner_bridge.plugin.live_job")
    def test_cleanup_timeout_still_reaps_and_attempts_deregistration(self, live, image, popen, docker):
        live.side_effect = [(job(), run()), (job(), run()), (dict(job("completed"), conclusion="success"), run())]
        github = GithubFixture()
        with self.assertRaisesRegex(BridgeError, "cleanup incomplete"):
            supervise(config(), github, 42)
        popen.return_value.wait.assert_called()
        self.assertTrue(any(call[1].endswith("/runners") for call in github.calls))

    def test_accepts_only_live_trusted_dispatch_and_exact_labels(self):
        self.assertTrue(eligible(config(), job(), run()))
        for field, value in (("event", "pull_request"), ("head_branch", "feature"),
                             ("path", ".github/workflows/other.yml"), ("run_attempt", 2),
                             ("repository", {"full_name": "other/project"})):
            bad = run()
            bad[field] = value
            self.assertFalse(eligible(config(), job(), bad), field)
        bad = job()
        bad["labels"].append("privileged")
        self.assertFalse(eligible(config(), bad, run()))
        self.assertFalse(eligible(config(), job("completed"), run()))

    def test_recovery_finds_queued_job_inside_in_progress_run(self):
        self.assertEqual([j["id"] for j in waiting_jobs(config(), GithubFixture())], [42])

    @patch("runner_bridge.plugin.command")
    def test_event_and_recovery_share_one_serialized_worker_step(self, command):
        event = {"action": "queued", "repository": {"full_name": "example/project"}, "workflow_job": job()}
        dispatch(config(), GithubFixture(), event)
        document = json.loads(command.call_args.kwargs["input"])
        self.assertEqual(len(document["steps"]), 1)
        worker = document["steps"][0]
        self.assertEqual(worker["agents"], {"queue": "linux"})
        self.assertEqual(worker["concurrency"], 1)
        self.assertEqual(worker["retry"]["automatic"], [{"exit_status": 75, "limit": 2}])
        self.assertIn("--no-interpolation", command.call_args.args[0])

    @patch("runner_bridge.plugin.command")
    def test_unrelated_and_completed_events_do_not_scan_or_upload(self, command):
        github = GithubFixture()
        dispatch(config(), github, {"action": "completed"})
        self.assertEqual(github.calls, [])
        command.assert_not_called()

    def test_duplicates_lock_together_but_new_attempts_do_not(self):
        first = step(config(), "run", job())
        second = step(config(), "run", copy.deepcopy(job()))
        self.assertEqual(first["concurrency_group"], second["concurrency_group"])
        self.assertNotEqual(identity("example/project", job()), identity("example/project", job(attempt=2)))

    @patch("runner_bridge.plugin.runner_image")
    def test_duplicate_or_cancelled_job_never_starts_docker(self, image):
        self.assertEqual(supervise(config(), GithubFixture([job("completed")]), 42), 0)
        image.assert_not_called()

    @patch("runner_bridge.plugin.subprocess.run")
    @patch("runner_bridge.plugin.subprocess.Popen")
    @patch("runner_bridge.plugin.runner_image", return_value="fixture-image")
    @patch("runner_bridge.plugin.live_job")
    def test_actual_workflow_failure_is_not_retried_as_infrastructure(self, live, image, popen, docker):
        failed = dict(job("completed"), conclusion="failure")
        live.side_effect = [(job(), run()), (job(), run()), (failed, run())]
        process = Mock()
        process.stdin = Mock()
        popen.return_value = process
        docker.return_value.returncode = 0
        self.assertEqual(supervise(config(), GithubFixture(), 42), 1)
        args = popen.call_args.args[0]
        self.assertNotIn("fixture-registration-token", " ".join(args))
        self.assertNotIn("-v", args)
        self.assertIn("fixture-registration-token", process.stdin.write.call_args.args[0].decode())
        self.assertEqual(docker.call_args.args[0][:3], ["docker", "rm", "-f"])

    @patch("runner_bridge.plugin.subprocess.run")
    @patch("runner_bridge.plugin.subprocess.Popen")
    @patch("runner_bridge.plugin.runner_image", return_value="fixture-image")
    @patch("runner_bridge.plugin.live_job")
    def test_startup_failure_is_retryable_and_container_is_removed(self, live, image, popen, docker):
        live.return_value = job(), run()
        popen.return_value.poll.return_value = 75
        docker.return_value.returncode = 0
        self.assertEqual(supervise(config(), GithubFixture(), 42), 75)
        self.assertEqual(docker.call_args.args[0][:3], ["docker", "rm", "-f"])

    @patch("runner_bridge.plugin.load_environment", return_value=(config(), GithubFixture()))
    @patch("runner_bridge.plugin.subprocess.run")
    @patch("runner_bridge.plugin.dispatch")
    def test_manual_build_recovers_but_metadata_failure_does_not(self, dispatch_mock, execute, load):
        with patch.dict(os.environ, {}, clear=True):
            execute.return_value.returncode = 100
            self.assertEqual(plugin_main(), 0)
            dispatch_mock.assert_called_once()
            dispatch_mock.reset_mock()
            execute.return_value.returncode = 1
            with self.assertRaises(BridgeError):
                plugin_main()
            dispatch_mock.assert_not_called()


class ConfigurationTests(unittest.TestCase):
    def test_pipeline_contains_pinned_plugin_and_no_secret(self):
        doc = json.loads(pipeline(config()))
        options = next(iter(doc["steps"][0]["plugins"][0].values()))
        restored = Config.load(options["config"])
        self.assertEqual(restored.repository, "example/project")
        self.assertNotIn("PRIVATE KEY", json.dumps(doc))

    def test_rejects_mutable_plugin_and_injected_paths(self):
        for values in ({"plugin": "example/plugin#main"}, {"repository": "../project"},
                       {"queue": "linux\ncommand: evil"}, {"image": "ubuntu:latest"}):
            data = config().data()
            data.update(values)
            with self.assertRaises(BridgeError):
                Config.load(data)

    def test_existing_workflow_edit_preserves_action_customizations(self):
        old = pullfrog_workflow(config(), "b" * 40, "c" * 40)
        old = old.replace('      - "pullfrog-${{ github.run_id }}-${{ github.run_attempt }}"\n', '')
        old = old.replace("          shell: restricted", "          shell: restricted\n          effort: high")
        new = patch_workflow(old, config())
        self.assertIn("effort: high", new)
        self.assertIn("id-token: write", new)
        self.assertIn("pullfrog-${{ github.run_id }}-${{ github.run_attempt }}", new)
        self.assertEqual(patch_workflow(new, config()), new)

    def test_unknown_workflow_is_not_replaced(self):
        with self.assertRaises(BridgeError):
            patch_workflow("on: issue_comment\njobs: {}\n", config())

    def test_runner_group_mapping_is_not_corrupted(self):
        text = pullfrog_workflow(config(), "b" * 40, "c" * 40)
        start = text.index("    runs-on:")
        end = text.index("    timeout-minutes:", start)
        text = text[:start] + "    runs-on:\n      group: example-group\n      labels: linux\n" + text[end:]
        with self.assertRaisesRegex(BridgeError, "mapping"):
            patch_workflow(text, config())

    def test_manifest_routes_app_webhooks_directly_to_buildkite(self):
        app = manifest(config(), "https://webhook.buildkite.com/deliver/fixture", "http://127.0.0.1/callback", "Example")
        self.assertFalse(app["public"])
        self.assertEqual(app["default_events"], ["workflow_job"])
        self.assertEqual(app["default_permissions"], {"administration": "write", "actions": "read"})


class BuildkiteFixture:
    def __init__(self):
        self.pipeline = None
        self.triggers = []
        self.calls = []
        self.fail_filter = False

    def pages(self, path):
        return iter(self.triggers)

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path.endswith("/triggers") and method == "POST":
            value = dict(data, id="trigger-1", endpoint_url="https://webhook.buildkite.com/deliver/fixture")
            self.triggers.append(value)
            return value
        if "/triggers/" in path:
            if self.fail_filter and data and "filter" in data:
                raise APIError(422, "Buildkite")
            return self.triggers[0]
        if method == "GET":
            if self.pipeline is None:
                raise APIError(404, "Buildkite")
            return self.pipeline
        if method == "POST":
            self.pipeline = dict(data, id="pipeline-1")
            return self.pipeline
        raise AssertionError((method, path))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = State(Path(self.temp.name) / "state.json")
        self.state.data["installation_uuid"] = "fixture-installation"
        self.bk = BuildkiteFixture()
        self.installer = Installer(config(), "example", "cluster-1", self.state, self.bk, Mock())

    def test_pipeline_and_trigger_are_reused_after_resume(self):
        self.installer.ensure_pipeline()
        self.installer.ensure_trigger()
        state = State(self.state.path)
        resumed = Installer(config(), "example", "cluster-1", state, self.bk, Mock())
        resumed.ensure_pipeline()
        resumed.ensure_trigger()
        self.assertEqual(sum(c[0] == "POST" for c in self.bk.calls), 2)
        self.assertEqual(self.state.path.stat().st_mode & 0o777, 0o600)

    def test_unmanaged_pipeline_is_never_overwritten(self):
        self.bk.pipeline = {"id": "other", "description": "Existing CI"}
        with self.assertRaisesRegex(BridgeError, "unmanaged"):
            self.installer.ensure_pipeline()
        self.assertFalse(any(c[0] in ("POST", "PATCH") for c in self.bk.calls))

    def test_lost_trigger_response_does_not_create_duplicate(self):
        self.bk.triggers = [{"label": "Runner bridge"}]
        with self.assertRaisesRegex(BridgeError, "saved endpoint"):
            self.installer.ensure_trigger()

    def test_filter_failure_is_not_silently_downgraded(self):
        self.installer.ensure_trigger()
        self.state.data["app"] = {"webhook_secret": "fixture"}
        self.bk.fail_filter = True
        with self.assertRaisesRegex(BridgeError, "--no-filter"):
            self.installer.configure_trigger(True)
        self.assertEqual(sum(c[0] == "PATCH" for c in self.bk.calls), 1)

    def test_world_readable_and_symlink_state_rejected(self):
        self.state.save()
        self.state.path.chmod(0o644)
        with self.assertRaises(BridgeError):
            State(self.state.path)
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.state.path)
        with self.assertRaises(BridgeError):
            State(link)

    def test_manifest_browser_callback_rejects_wrong_state(self):
        self.state.data["app_name"] = "Example Runner Bridge"
        api = Mock()
        api.request.return_value = {"id": 1, "pem": "fixture-only"}
        errors = []
        threads = []
        def opener(url):
            def browser():
                try:
                    with urllib.request.urlopen(url, timeout=5) as response:
                        self.assertIn(b"Create the preconfigured", response.read())
                    parsed = urllib.parse.urlsplit(url)
                    base = f"{parsed.scheme}://{parsed.netloc}/callback"
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(base + "?state=wrong&code=bad", timeout=5)
                    self.assertEqual(rejected.exception.code, 400)
                    nonce = urllib.parse.parse_qs(parsed.query)["state"][0]
                    with urllib.request.urlopen(base + "?state=" + nonce + "&code=fixture-code", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                except BaseException as exc:
                    errors.append(exc)
            thread = threading.Thread(target=browser, daemon=True)
            threads.append(thread)
            thread.start()
        with patch("builtins.print"):
            result = register_app(config(), "https://webhook.buildkite.com/deliver/fixture", "Organization", self.state, api, opener)
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        self.assertEqual(result["id"], 1)
        api.request.assert_called_once_with("POST", "/app-manifests/fixture-code/conversions", {})


class EntrypointTests(unittest.TestCase):
    def test_registration_is_ephemeral_without_default_labels_and_secret_not_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "config.sh").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > config-args\n')
            (work / "run.sh").write_text('#!/bin/sh\nprintf "runner started\\n"\n')
            for name in ("config.sh", "run.sh"):
                (work / name).chmod(0o700)
            entry = Path(__file__).resolve().parent.parent / "runner/entrypoint.py"
            result = subprocess.run([os.sys.executable, str(entry)], cwd=work, capture_output=True, text=True,
                                    input=json.dumps({"url": "https://github.com/example/project", "token": "fixture-secret",
                                                      "name": "fixture-runner", "labels": ["fixture-label"]}) + "\n")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "runner started\n")
            self.assertNotIn("fixture-secret", result.stdout + result.stderr)
            args = (work / "config-args").read_text().splitlines()
            self.assertIn("--ephemeral", args)
            self.assertIn("--no-default-labels", args)


class AuthTests(unittest.TestCase):
    def test_app_jwt_is_real_rs256_and_short_lived(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "test.pem"
            public = Path(directory) / "public.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private)],
                           check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, capture_output=True)
            token = app_jwt("123", private.read_text())
            header, payload, signature = token.split(".")
            decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            claims = json.loads(decode(payload))
            self.assertEqual(claims["iss"], "123")
            self.assertLessEqual(claims["exp"] - claims["iat"], 600)
            sig = Path(directory) / "sig"
            sig.write_bytes(decode(signature))
            verified = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(sig)],
                                      input=f"{header}.{payload}".encode(), capture_output=True)
            self.assertEqual(verified.returncode, 0)

    def test_credentials_cannot_be_sent_to_arbitrary_api_origin(self):
        with self.assertRaises(BridgeError):
            API("https://untrusted.example", "fixture-token")


if __name__ == "__main__":
    unittest.main()
