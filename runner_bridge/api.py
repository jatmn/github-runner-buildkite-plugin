"""Small HTTPS clients. Never echo response bodies or credentials in errors."""
import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


class BridgeError(Exception):
    pass


class APIError(BridgeError):
    def __init__(self, status, service):
        self.status = status
        super().__init__(f"{service} request failed (HTTP {status}); check permissions and service status")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, base, token=""):
        if base not in ("https://api.github.com", "https://api.buildkite.com/v2"):
            raise BridgeError("Only GitHub.com and Buildkite API endpoints are supported")
        self.base, self.token = base, token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, data=None):
        if not path.startswith("/") or path.startswith("//"):
            raise BridgeError("Invalid API path")
        headers = {"Accept": "application/json", "User-Agent": "buildkite-runner-bridge/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if "github" in self.base:
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            try:
                req = urllib.request.Request(self.base + path, body, headers, method=method)
                with self.opener.open(req, timeout=30) as response:
                    raw = response.read()
                return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                if method == "GET" and exc.code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise APIError(exc.code, urllib.parse.urlsplit(self.base).hostname) from None
            except (urllib.error.URLError, TimeoutError):
                if method == "GET" and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise BridgeError("API connection failed; rerun to reconcile before retrying mutations") from None

    def pages(self, path, key=None):
        for page in range(1, 1001):
            sep = "&" if "?" in path else "?"
            result = self.request("GET", f"{path}{sep}per_page=100&page={page}")
            items = result[key] if key else result
            if not isinstance(items, list):
                raise BridgeError("Unexpected paginated API response")
            yield from items
            if len(items) < 100:
                return
        raise BridgeError("API pagination limit reached; narrow configured repositories")


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def app_jwt(app_id, private_key):
    now = int(time.time())
    message = b64(b'{"alg":"RS256","typ":"JWT"}') + "." + b64(json.dumps({
        "iat": now - 60, "exp": now + 540, "iss": str(app_id)
    }).encode())
    with tempfile.NamedTemporaryFile(mode="w", prefix="bridge-key-", suffix=".pem") as key:
        os.chmod(key.name, 0o600)
        key.write(private_key)
        key.flush()
        proc = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key.name],
                              input=message.encode(), capture_output=True, check=False)
    if proc.returncode:
        raise BridgeError("Could not sign GitHub App token; check OpenSSL and the App private key")
    return message + "." + b64(proc.stdout)


class GitHubApp(API):
    def __init__(self, app_id, installation_id, private_key):
        super().__init__("https://api.github.com")
        self.app_id, self.installation_id, self.private_key = app_id, installation_id, private_key
        self.refresh_at = 0

    def request(self, method, path, data=None):
        if time.monotonic() >= self.refresh_at:
            issuer = API(self.base, app_jwt(self.app_id, self.private_key))
            result = issuer.request("POST", f"/app/installations/{self.installation_id}/access_tokens", {})
            self.token = result["token"]
            self.refresh_at = time.monotonic() + 2700
        return super().request(method, path, data)
