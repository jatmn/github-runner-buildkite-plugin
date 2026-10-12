"""Event-driven dispatcher and supervised, one-job Docker runner."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from .api import APIError, BridgeError, GitHubApp
from .config import Config, identity, run_label, step

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "BUILDKITE_PLUGIN_GITHUB_RUNNER_"


def command(args, *, input=None, quiet=False):
    result = subprocess.run(args, input=input, text=True, capture_output=True, check=False)
    if result.returncode:
        raise BridgeError(f"{args[0]} operation failed (exit {result.returncode}); inspect the tool configuration")
    if not quiet:
        return result.stdout
    return ""


def load_environment():
    config = Config.load(os.environ.get(PREFIX + "CONFIG", "{}"))
    if not config.app_id or not config.installation_id:
        raise BridgeError("App ID and installation ID are required")
    key = command(["buildkite-agent", "secret", "get", config.key_secret]).strip()
    if "PRIVATE KEY" not in key:
        raise BridgeError("App key secret is not a PEM private key")
    return config, GitHubApp(config.app_id, config.installation_id, key)


def eligible(config, job, run):
    """Authorize from live API objects, never from the webhook alone."""
    expected = {config.label, run_label(job)}
    return (job.get("status") == "queued"
            and set(job.get("labels", [])) == expected
            and run.get("path", "").split("@")[0] == config.workflow
            and run.get("event") == "workflow_dispatch"
            and run.get("head_branch") == config.branch
            and run.get("repository", {}).get("full_name", "").lower() == config.repository.lower()
            and int(run.get("run_attempt", 0)) == int(job["run_attempt"]))


def live_job(config, github, job_id):
    job = github.request("GET", f"/repos/{config.repository}/actions/jobs/{int(job_id)}")
    run = github.request("GET", f"/repos/{config.repository}/actions/runs/{int(job['run_id'])}")
    return job, run


def waiting_jobs(config, github):
    seen = set()
    # A run can be in_progress while another job in it waits for a runner.
    for status in ("queued", "in_progress", "pending", "waiting", "requested"):
        path = f"/repos/{config.repository}/actions/runs?status={status}"
        for run in github.pages(path, "workflow_runs"):
            if run["id"] in seen:
                continue
            seen.add(run["id"])
            for job in github.pages(f"/repos/{config.repository}/actions/runs/{int(run['id'])}/jobs?filter=latest", "jobs"):
                if eligible(config, job, run):
                    yield job


def dispatch(config, github, payload=None):
    candidates = {}
    if payload is not None:
        if (payload.get("action") != "queued"
                or payload.get("repository", {}).get("full_name", "").lower() != config.repository.lower()
                or config.label not in payload.get("workflow_job", {}).get("labels", [])):
            print("Ignored unrelated webhook")
            return
        job, run = live_job(config, github, payload["workflow_job"]["id"])
        if eligible(config, job, run):
            candidates[job["id"]] = job
    for job in waiting_jobs(config, github):
        candidates[job["id"]] = job
    if not candidates:
        print("No waiting bridge jobs")
        return
    generated = {"steps": [step(config, "run", job) for job in candidates.values()]}
    # Each worker holds an organization-wide per-job concurrency group. Repeated
    # deliveries may enqueue redundant steps; live revalidation makes them no-ops.
    command(["buildkite-agent", "pipeline", "upload", "--no-interpolation"],
            input=json.dumps(generated), quiet=True)
    print(f"Scheduled {len(candidates)} runner candidate(s); duplicates revalidate before starting")


def runner_image(config):
    if config.image != "bundled":
        return config.image
    import hashlib
    directory = ROOT / "runner"
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file())).hexdigest()[:16]
    image = "buildkite-github-runner:" + digest
    existing = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    if existing.returncode:
        command(["docker", "build", "--platform", "linux/amd64", "-t", image, str(directory)], quiet=True)
    return image


def sandbox_args(config):
    # Explicit operator opt-in. No privileged container or host Docker socket.
    return ["--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined"] if config.relax_container_sandbox else []


def doctor(config):
    image = runner_image(config)
    command(["docker", "run", "--rm", *sandbox_args(config), "--entrypoint", "bash", image,
             "-c", "sudo -n true && unshare --user --map-root-user --pid --fork --mount true"], quiet=True)
    print("Runner image, sudo and user/PID/mount namespaces are available")


def supervise(config, github, job_id):
    job, run = live_job(config, github, job_id)
    if not eligible(config, job, run):
        print("Job no longer eligible; no runner started")
        return 0
    image = runner_image(config)
    # Image preparation may be slow. Check again before issuing credentials.
    job, run = live_job(config, github, job_id)
    if not eligible(config, job, run):
        return 0
    name = "bkgh-" + identity(config.repository, job) + "-" + uuid.uuid4().hex[:8]
    token = github.request("POST", f"/repos/{config.repository}/actions/runners/registration-token", {})["token"]
    registration = {"url": "https://github.com/" + config.repository, "token": token,
                    "name": name, "labels": [config.label, run_label(job)]}
    args = ["docker", "run", "--rm", "--init", "-i", "--name", name,
            "--label", "io.buildkite.runner-bridge=true", *sandbox_args(config), image]
    previous_handlers = {}
    proc = None
    def interrupted(signum, frame):
        raise InterruptedError("Buildkite job stopped")
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.signal(sig, interrupted)
        proc = subprocess.Popen(args, stdin=subprocess.PIPE)
        proc.stdin.write((json.dumps(registration) + "\n").encode())
        proc.stdin.close()
        registration.clear()
        token = None
        startup_deadline = time.monotonic() + config.startup_seconds
        running_deadline = None
        status_failures = 0
        while True:
            try:
                current, current_run = live_job(config, github, job_id)
                status_failures = 0
            except BridgeError:
                status_failures += 1
                print("Could not read GitHub job status; retrying monitoring")
                if (status_failures >= 3 or proc.poll() is not None
                        or time.monotonic() > (running_deadline or startup_deadline)):
                    print("Monitoring unavailable beyond its bounded allowance")
                    return 1
                time.sleep(10)
                continue
            if current["status"] == "completed":
                print("GitHub job conclusion: " + str(current.get("conclusion")))
                # Give action post steps/runner shutdown a short opportunity to finish.
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    pass
                return 0 if current.get("conclusion") in ("success", "cancelled", "skipped") else 1
            if current["status"] == "in_progress":
                if current.get("runner_name") != name:
                    print("Another runner acquired the job; releasing this container")
                    return 0
                if running_deadline is None:
                    running_deadline = time.monotonic() + config.execution_seconds
            if int(current_run.get("run_attempt", 0)) != int(job["run_attempt"]):
                return 0
            if proc.poll() is not None:
                print("Runner exited before GitHub reported completion")
                return 75 if current["status"] == "queued" else 1
            if time.monotonic() > (running_deadline or startup_deadline):
                print("Runner deadline reached")
                return 75 if current["status"] == "queued" else 1
            time.sleep(10)
    except InterruptedError:
        print("Provisioning cancelled; cleaning up owned runner")
        return 1
    except (BrokenPipeError, FileNotFoundError):
        raise BridgeError("Docker runner bootstrap failed before credentials were accepted") from None
    finally:
        # Stop only this invocation's random, exact container name.
        cleanup_failed = False
        for sig in previous_handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            removal = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            if removal.returncode:
                # --rm may already have removed the container. Distinguish that
                # from a daemon outage rather than hiding all rm errors.
                inspect = subprocess.run(["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"],
                                         capture_output=True, timeout=15)
                cleanup_failed = bool(inspect.returncode or inspect.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            cleanup_failed = True
        if proc:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        try:
            for runner in github.pages(f"/repos/{config.repository}/actions/runners", "runners"):
                if runner.get("name") == name:
                    github.request("DELETE", f"/repos/{config.repository}/actions/runners/{int(runner['id'])}")
        except BridgeError:
            print("Runner deregistration failed; inspect repository runner settings for an offline bkgh runner")
            cleanup_failed = True
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if cleanup_failed:
            raise BridgeError("Owned runner cleanup incomplete; inspect the Buildkite worker and repository runner settings")


def plugin_main():
    config, github = load_environment()
    mode = os.environ.get(PREFIX + "MODE", "dispatch")
    if os.environ.get("RUNNER_BRIDGE_DIAGNOSTIC") == "1":
        mode = "doctor"
    if mode == "run":
        try:
            return supervise(config, github, int(os.environ[PREFIX + "JOB_ID"]))
        except BridgeError as exc:
            # The next attempt must revalidate live state before creating a runner.
            print("Runner infrastructure operation failed: " + str(exc))
            return 75
    if mode == "doctor":
        doctor(config)
        return 0
    if mode not in ("dispatch", "recover"):
        raise BridgeError("Unsupported plugin mode")
    payload = None
    if mode == "dispatch":
        exists = subprocess.run(["buildkite-agent", "meta-data", "exists", "buildkite:webhook"], capture_output=True)
        if exists.returncode == 0:
            payload = json.loads(command(["buildkite-agent", "meta-data", "get", "buildkite:webhook"]))
        elif exists.returncode != 100:
            # Buildkite uses 100 for a missing key; don't interpret API failure as recovery.
            raise BridgeError("Could not determine webhook metadata; start explicit recovery mode")
    dispatch(config, github, payload)
    return 0
