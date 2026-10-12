"""Resumable installer. Account approval happens in the user's browser."""
import base64
import getpass
import hashlib
import html
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import urllib.parse
import webbrowser

from .api import API, APIError, BridgeError, app_jwt
from .config import Config, pipeline, pullfrog_workflow, step


class GitHubUser:
    """Use the user's existing gh session, without copying its token to disk."""
    def request(self, method, path, data=None):
        args = ["gh", "api", path.lstrip("/"), "--method", method]
        if data is not None:
            args += ["--input", "-"]
        result = subprocess.run(args, input=json.dumps(data) if data is not None else None,
                                text=True, capture_output=True)
        if result.returncode:
            if "HTTP 404" in result.stderr:
                raise APIError(404, "GitHub")
            raise BridgeError("GitHub CLI request failed; check gh auth status and repository/workflow write permissions")
        return json.loads(result.stdout) if result.stdout.strip() else None


class State:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.data = {}
        if self.path.is_symlink():
            raise BridgeError("Refusing a symlink for setup state")
        if self.path.exists():
            if self.path.stat().st_mode & 0o077:
                raise BridgeError("Setup state contains credentials; restrict its permissions to 0600")
            self.data = json.loads(self.path.read_text())

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.data, stream, indent=2)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def manifest(config, endpoint, redirect, name):
    return {"name": name, "url": "https://github.com/" + config.plugin.split("#")[0] + "-buildkite-plugin",
            "public": False, "redirect_url": redirect,
            "hook_attributes": {"url": endpoint, "active": True},
            "default_permissions": {"administration": "write", "actions": "read"},
            "default_events": ["workflow_job"]}


def register_app(config, endpoint, owner_type, state, api=None, opener=webbrowser.open):
    api = api or API("https://api.github.com")
    nonce = secrets.token_urlsafe(32)
    owner = config.repository.split("/")[0]
    create_url = (f"https://github.com/organizations/{owner}/settings/apps/new" if owner_type == "Organization"
                  else "https://github.com/settings/apps/new")
    result = {}
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # The callback contains a credential. Never log request URLs.

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/start" and query.get("state") == [nonce]:
                app = manifest(config, endpoint, f"http://127.0.0.1:{server.server_port}/callback",
                               state.data["app_name"])
                form = (f'<form method="post" action="{html.escape(create_url)}?state={nonce}">'
                        f'<input type="hidden" name="manifest" value="{html.escape(json.dumps(app), quote=True)}">'
                        '<button>Create the preconfigured GitHub App</button></form>')
                body = ("<!doctype html><title>Runner bridge setup</title><h1>Connect GitHub</h1>" + form).encode()
                self.send_response(200)
            elif parsed.path == "/callback" and query.get("state") == [nonce] and query.get("code"):
                result["code"] = query["code"][0]
                body = b"Return to your terminal to finish installation."
                self.send_response(200)
            else:
                self.send_response(400)
                body = b"Invalid setup request."
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; form-action https://github.com; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
    with http.server.HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        url = f"http://127.0.0.1:{server.server_port}/start?state={nonce}"
        print("Opening a local setup page. If necessary, open this on the same computer:\n" + url)
        opener(url)
        deadline = time.monotonic() + 600
        while "code" not in result and time.monotonic() < deadline:
            server.handle_request()
        if "code" not in result:
            raise BridgeError("Browser approval timed out; rerun setup")
    code = urllib.parse.quote(result["code"], safe="")
    return api.request("POST", f"/app-manifests/{code}/conversions", {})


def patch_workflow(text, config):
    """Conservative edit of the documented Pullfrog layout; never guess at YAML."""
    if "workflow_dispatch:" not in text or not re.search(r"prompt:\s*\$\{\{\s*inputs.prompt\s*\}\}", text):
        raise BridgeError("Existing workflow is not the standard Pullfrog dispatch workflow; migrate it through Pullfrog first")
    match = re.search(r"(?m)^  pullfrog:\s*\n", text)
    if not match:
        raise BridgeError("Expected the standard jobs.pullfrog layout; no workflow overwritten")
    end = re.search(r"(?m)^\S|^  [A-Za-z_][A-Za-z0-9_-]*:", text[match.end():])
    stop = match.end() + end.start() if end else len(text)
    block = text[match.end():stop]
    route = re.search(r"(?m)^    runs-on:([^\n]*)\n(?:      -[^\n]*\n)*", block)
    if not route or route.group(1).strip().startswith(("|", ">")):
        raise BridgeError("Unsupported runs-on layout; no workflow overwritten")
    remainder = block[route.end():]
    if re.match(r"(?:[ \t]*\n|[ \t]*#[^\n]*\n)*      \S", remainder):
        raise BridgeError("Unsupported runs-on mapping or nested layout; no workflow overwritten")
    replacement = ("    runs-on:\n"
                   f"      - {config.label}\n      - \"pullfrog-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}\"\n")
    block = block[:route.start()] + replacement + block[route.end():]
    return text[:match.end()] + block + text[stop:]


class Installer:
    def __init__(self, config, organization, cluster, state, buildkite, github):
        self.config, self.organization, self.cluster = config, organization, cluster
        self.state, self.bk, self.gh = state, buildkite, github
        self.slug = "github-runner-" + hashlib.sha256(config.repository.lower().encode()).hexdigest()[:12]
        self.base = f"/organizations/{organization}/pipelines/{self.slug}"

    def checkpoint(self, key, value):
        self.state.data[key] = value
        self.state.save()
        return value

    def ensure_pipeline(self):
        marker = "Managed by buildkite-runner-bridge; installation " + self.state.data["installation_uuid"]
        try:
            existing = self.bk.request("GET", self.base)
        except APIError as exc:
            if exc.status != 404:
                raise
            existing = None
        if existing:
            if existing.get("description") != marker:
                raise BridgeError("Pipeline name collision: refusing to modify an unmanaged pipeline")
        else:
            existing = self.bk.request("POST", f"/organizations/{self.organization}/pipelines", {
                "name": self.slug, "slug": self.slug, "description": marker, "cluster_id": self.cluster,
                "repository": "https://github.com/" + self.config.repository,
                "default_branch": self.config.branch,
                "configuration": pipeline(self.config),
                "provider_settings": {"build_branches": False, "build_pull_requests": False,
                                      "build_tags": False, "publish_commit_status": False}})
        self.checkpoint("pipeline_id", existing["id"])
        return existing

    def ensure_trigger(self):
        if self.state.data.get("trigger_id"):
            self.bk.request("GET", self.base + "/triggers/" + self.state.data["trigger_id"])
            return self.state.data["endpoint"]
        # A lost POST response may have created a trigger. Its URL is returned
        # only once. Refuse to create duplicates when we cannot recover it.
        existing = list(self.bk.pages(self.base + "/triggers"))
        if any(t.get("label") == "Runner bridge" for t in existing):
            raise BridgeError("A bridge trigger exists without saved endpoint state; inspect it before retrying setup")
        secret = secrets.token_urlsafe(32)
        trigger = self.bk.request("POST", self.base + "/triggers", {
            "type": "github", "label": "Runner bridge", "enabled": False,
            "build": {"branch": self.config.branch, "commit": "HEAD", "message": "GitHub runner request"},
            "verification": {"strategy": "hmac", "secret": secret}})
        self.state.data.update(trigger_id=trigger["id"], endpoint=trigger["endpoint_url"])
        self.state.save()
        return trigger["endpoint_url"]

    def save_key(self):
        base = f"/organizations/{self.organization}/clusters/{self.cluster}/secrets"
        policy = f"- pipeline_slug: {self.slug}\n  build_branch: {json.dumps(self.config.branch)}"
        known = [s for s in self.bk.pages(base) if s["key"] == self.config.key_secret]
        description = "Runner bridge " + self.state.data["installation_uuid"]
        if known:
            target = known[0]
            if target.get("description") != description:
                raise BridgeError("Secret name collision: refusing to overwrite an unmanaged secret")
            self.bk.request("PUT", base + "/" + target["id"] + "/value", {"value": self.state.data["app"]["pem"]})
            self.bk.request("PUT", base + "/" + target["id"], {"policy": policy})
        else:
            target = self.bk.request("POST", base, {"key": self.config.key_secret,
                "value": self.state.data["app"]["pem"], "description": description, "policy": policy})
        self.checkpoint("secret_id", target["id"])

    def configure_trigger(self, filtering):
        data = {"enabled": True, "verification": {"strategy": "hmac", "secret": self.state.data["app"]["webhook_secret"]}}
        if filtering:
            data["filter"] = {"expression": 'webhook.payload.action == "queued" && "' + self.config.label + '" in webhook.payload.workflow_job.labels'}
        else:
            data["filter"] = None
        try:
            self.bk.request("PATCH", self.base + "/triggers/" + self.state.data["trigger_id"], data)
        except APIError as exc:
            if filtering and exc.status == 422:
                raise BridgeError("Trigger filter unavailable or rejected. Rerun with --no-filter to accept short no-op builds for other events") from None
            raise

    def workflow_pr(self):
        repo = self.config.repository
        branch = "runner-bridge/setup-" + self.state.data["installation_uuid"][:8]
        default = urllib.parse.quote(self.config.branch, safe="")
        current = None
        try:
            current = self.gh.request("GET", f"/repos/{repo}/contents/{self.config.workflow}?ref={default}")
        except APIError as exc:
            if exc.status != 404:
                raise
        if current:
            old = base64.b64decode(current["content"]).decode()
            content = patch_workflow(old, self.config)
            if content == old:
                self.checkpoint("workflow_ready", True)
                return None
        else:
            frog = self.gh.request("GET", "/repos/pullfrog/pullfrog/commits/v0")["sha"]
            checkout = self.gh.request("GET", "/repos/actions/checkout/commits/v6")["sha"]
            content = pullfrog_workflow(self.config, frog, checkout)
        head_sha = self.gh.request("GET", f"/repos/{repo}/git/ref/heads/{default}")["object"]["sha"]
        try:
            self.gh.request("GET", f"/repos/{repo}/git/ref/heads/{branch}")
        except APIError as exc:
            if exc.status != 404:
                raise
            self.gh.request("POST", f"/repos/{repo}/git/refs", {"ref": "refs/heads/" + branch, "sha": head_sha})
        branch_file = None
        try:
            branch_file = self.gh.request("GET", f"/repos/{repo}/contents/{self.config.workflow}?ref={branch}")
        except APIError as exc:
            if exc.status != 404:
                raise
        existing_text = base64.b64decode(branch_file["content"]).decode() if branch_file else None
        previous_hash = self.state.data.get("workflow_content_hash")
        if existing_text is not None and existing_text != content:
            existing_hash = hashlib.sha256(existing_text.encode()).hexdigest()
            default_text = base64.b64decode(current["content"]).decode() if current else None
            if (previous_hash and existing_hash != previous_hash) or (not previous_hash and existing_text != default_text):
                raise BridgeError("Setup branch workflow was edited; refusing to overwrite customer changes")
        if existing_text != content:
            data = {"message": "Route Pullfrog jobs to the Buildkite runner pool", "branch": branch,
                    "content": base64.b64encode(content.encode()).decode()}
            if branch_file:
                data["sha"] = branch_file["sha"]
            self.gh.request("PUT", f"/repos/{repo}/contents/{self.config.workflow}", data)
        self.checkpoint("workflow_content_hash", hashlib.sha256(content.encode()).hexdigest())
        prs = self.gh.request("GET", f"/repos/{repo}/pulls?head={repo.split('/')[0]}:{branch}&state=open")
        if prs:
            return self.checkpoint("workflow_pr", prs[0]["html_url"])
        pr = self.gh.request("POST", f"/repos/{repo}/pulls", {
            "title": "Run Pullfrog on the existing Buildkite pool", "head": branch,
            "base": self.config.branch, "draft": True,
            "body": "Routes Pullfrog's normal dispatch workflow to temporary GitHub runners provisioned by Buildkite.\n\n"
                    "Before merging: install Pullfrog for this repository, connect the model provider, disable unwanted automatic tasks, "
                    "set code pushes to disabled for review-only use, and run the bridge diagnostic build. "
                    "No end-to-end review has been executed by the installer."})
        return self.checkpoint("workflow_pr", pr["html_url"])

    def diagnostic(self):
        if self.state.data.get("diagnostic_build"):
            return self.state.data["diagnostic_build"]
        build = self.bk.request("POST", self.base + "/builds", {
            "commit": "HEAD", "branch": self.config.branch, "message": "Runner bridge infrastructure diagnostic",
            "env": {"RUNNER_BRIDGE_DIAGNOSTIC": "1"}})
        return self.checkpoint("diagnostic_build", build["web_url"])


def setup(args):
    state = State(args.state)
    gh = GitHubUser()
    repo = args.repo or state.data.get("repository") or input("GitHub repository (OWNER/REPO): ").strip()
    organization = args.buildkite_org or state.data.get("organization") or input("Buildkite organization: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", organization):
        raise BridgeError("Invalid Buildkite organization")
    # Validate owner/repo before using it as an API path.
    from .config import REPO
    if not REPO.fullmatch(repo) or any(x in (".", "..") for x in repo.split("/")):
        raise BridgeError("Repository must be OWNER/REPO")
    metadata = gh.request("GET", "/repos/" + repo)
    if not metadata.get("permissions", {}).get("admin"):
        raise BridgeError("Setup requires repository administrator access")
    if args.plan:
        print("Plan only: create a customer-owned App, signed incoming trigger, secret, Buildkite pipeline and workflow PR.")
        print("Existing workers require Linux x64, Python 3.11+, OpenSSL, Docker and compatible namespaces.")
        print("No resources were changed. Run without --plan to choose the cluster and queue.")
        return
    token = os.environ.get("BUILDKITE_API_TOKEN") or getpass.getpass("Buildkite setup API token (hidden): ")
    bk = API("https://api.buildkite.com/v2", token)
    clusters = list(bk.pages(f"/organizations/{organization}/clusters"))
    cluster = args.cluster or state.data.get("cluster")
    if not cluster:
        for item in clusters:
            print(f"  {item['id']}  {item.get('name', '')}")
        cluster = input("Cluster ID: ").strip()
    if not any(c["id"] == cluster for c in clusters):
        raise BridgeError("Cluster not found")
    queues = list(bk.pages(f"/organizations/{organization}/clusters/{cluster}/queues"))
    queue = args.queue or state.data.get("queue")
    if not queue:
        print("Queues: " + ", ".join(q["key"] for q in queues))
        queue = input("Existing queue key: ").strip()
    if not any(q["key"] == queue for q in queues):
        raise BridgeError("Queue not found")
    # Infer the distribution repository from an explicitly supplied source, never
    # from private working-directory Git configuration.
    source = args.source or state.data.get("source")
    if not source or not REPO.fullmatch(source) or source.split("/")[-1] != "github-runner-buildkite-plugin":
        raise BridgeError("Pass --source OWNER/github-runner-buildkite-plugin (the public plugin repository)")
    revision = args.revision or state.data.get("revision")
    if not revision:
        revision = gh.request("GET", f"/repos/{source}/commits/main")["sha"]
    install_id = state.data.get("installation_uuid") or secrets.token_hex(16)
    config = Config(repository=repo, queue=queue, plugin=source.removesuffix("-buildkite-plugin") + "#" + revision,
                    branch=metadata["default_branch"], key_secret="RUNNER_BRIDGE_KEY_" + install_id[:12].upper(),
                    image=args.image or state.data.get("image", "bundled"),
                    relax_container_sandbox=args.relax_container_sandbox or state.data.get("relax_container_sandbox", False)).validate()
    expected = {"repository": repo, "organization": organization, "cluster": cluster, "queue": queue, "source": source}
    if any(k in state.data and state.data[k] != v for k, v in expected.items()):
        raise BridgeError("State belongs to a different installation; select a separate --state file")
    print(f"Create/update the runner bridge for {repo} on queue {queue}; propose workflow changes in a draft PR.")
    if not args.yes and input("Proceed with this setup? [y/N] ").strip().lower() != "y":
        return
    state.data.update(expected, installation_uuid=install_id, revision=revision, image=config.image,
                      relax_container_sandbox=config.relax_container_sandbox,
                      app_name=state.data.get("app_name") or "Runner Bridge " + install_id[:10])
    state.save()
    installer = Installer(config, organization, cluster, state, bk, gh)
    installer.ensure_pipeline()
    endpoint = installer.ensure_trigger()
    if not state.data.get("app"):
        if state.data.get("app_registration_started"):
            raise BridgeError("Previous App registration did not save credentials. Inspect the named App in GitHub before removing app_registration_started from private state and retrying")
        installer.checkpoint("app_registration_started", True)
        app = register_app(config, endpoint, metadata["owner"]["type"], state)
        # Store only fields needed for recovery, not OAuth client secrets.
        installer.checkpoint("app", {k: app[k] for k in ("id", "slug", "pem", "webhook_secret")})
    app = state.data["app"]
    print("Approve installation on the selected repository:")
    install_url = f"https://github.com/apps/{app['slug']}/installations/new"
    print(install_url)
    if not state.data.get("github_installation_id"):
        webbrowser.open(install_url)
        input("Press Enter after installing the App: ")
    admin = API("https://api.github.com", app_jwt(app["id"], app["pem"]))
    installation = admin.request("GET", f"/repos/{repo}/installation")
    installer.checkpoint("github_installation_id", installation["id"])
    config.app_id = str(app["id"])
    config.installation_id = str(installation["id"])
    installer.save_key()
    bk.request("PATCH", installer.base, {"configuration": pipeline(config)})
    installer.configure_trigger(not args.no_filter)
    pr = installer.workflow_pr()
    if pr:
        print("Review and merge the draft workflow PR: " + pr)
    diagnostic = installer.diagnostic()
    print("Infrastructure diagnostic (check its result): " + diagnostic)
    print("Finish Pullfrog App/provider setup and disable automatic tasks in its console, then request a review.")
    print("No model-using review was started. Manual recovery: New Build on this pipeline's default branch.")
    print("State contains credentials: keep it private. Rerunning setup resumes this installation.")
