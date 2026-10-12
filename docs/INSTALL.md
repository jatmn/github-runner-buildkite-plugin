# Installation

This is an experimental implementation. Start with a disposable repository and
test queue. Setup creates resources; it does not certify a deployment.

## Before running setup

On your computer, install Python 3.11+, OpenSSL and GitHub CLI. Sign in with
`gh auth login`. Your session must administer the target repository, create a
branch/PR and update workflow files. For classic GitHub OAuth tokens this includes
the `workflow` scope. An organization may require owner approval to create or
install the integration App.

Use a Buildkite API token with access to the selected organization and these
scopes: `read_organizations`, `read_clusters`, `read_pipelines`, `write_pipelines`,
`read_secrets_details`, `write_secrets`, and `write_builds`. The selected identity
must have permission to manage the cluster's secrets and pipeline. Queue listing
is under the cluster API. The token is used only by the installer and is not
saved in its state or passed to reviews. Set `BUILDKITE_API_TOKEN` securely or
enter it at the hidden prompt. Do not put a token in shell arguments/history.

Workers in the chosen queue need Linux x64, Python 3.11+, OpenSSL, Docker,
Buildkite plugins enabled, `buildkite-agent secret get`, and outbound HTTPS to
GitHub, Buildkite, Ubuntu package mirrors (for bundled image builds), Pullfrog
and the model provider. No incoming port is required on workers.

## Start the command

Substitute the public project repository for `OWNER`. With uv:

```bash
uvx --from 'git+https://github.com/OWNER/github-runner-buildkite-plugin.git' \
  bk-runner-bridge setup --source OWNER/github-runner-buildkite-plugin \
  --repo example/project --buildkite-org example-org --queue linux
```

Alternatively, clone the public project, install it with `python3 -m pip install .`
in a virtual environment, then run `bk-runner-bridge setup` with the same options.
Pass `--revision FULL_COMMIT_SHA` to choose an immutable plugin version. Without
it, setup resolves the source repository's current `main` once and records it.
The plugin reference must use this repository's `github-runner` name because
Buildkite derives the hook option environment prefix from that name.

Optional switches:

| Switch | Purpose |
| --- | --- |
| `--plan` | Read-only GitHub preflight; no resource creation |
| `--cluster ID` | Skip the cluster selection prompt |
| `--state PATH` | Separate private state for another installation |
| `--no-filter` | Accept no-op builds when trigger filtering is unavailable |
| `--image REGISTRY/IMAGE@sha256:DIGEST` | Use your own image implementing the bundled entrypoint contract |
| `--relax-container-sandbox` | Explicitly disable Docker seccomp/AppArmor profiles; see below |
| `--yes` | Accept resource creation; account consent still requires the browser |

## What happens

1. Validate repository administrator access and discover the existing cluster
   and queue. Show the proposed installation and ask before mutations.
2. Create a dedicated pipeline with a commit-pinned plugin. Disable ordinary
   source-provider push, tag and PR builds for this pipeline.
3. Create a **disabled**, signed incoming trigger of type `github`.
4. Open a temporary page bound to `127.0.0.1`. Click to create the preconfigured
   customer-owned GitHub App. Its `workflow_job` webhook goes directly to the
   Buildkite endpoint. The local callback has a random state check and shuts
   down after approval or timeout. Run this step on the browser's own computer.
5. Approve App installation for the selected repository. The installer discovers
   its installation ID, saves its key in Buildkite Secrets, restricts that secret
   to the generated pipeline/default branch, and configures HMAC verification.
6. Enable the incoming trigger and optional queued-job/label filter. If filtering
   is unavailable, setup stops and explains `--no-filter`; it never silently
   changes the filtering policy. Incoming triggers are currently a Buildkite
   public-preview dependency.
7. Create a draft PR routing Pullfrog to bridge-specific and run-specific labels.
   If a standard Pullfrog workflow exists, edit only its runner routing. Unknown
   or custom command workflows are refused rather than replaced. Existing model
   and security settings remain the operator's responsibility.
8. Queue a diagnostic build to check image startup, sudo and namespaces. Inspect
   that build's result. Setup does not equate a submitted diagnostic with success.

The App requests repository Administration write for runner lifecycle operations,
Actions read for job inspection, and implicit Metadata read. No App contents/write
permission is needed at runtime; workflow PR creation uses your local `gh` session.

## Pullfrog

Install Pullfrog through its console and connect your model/provider credentials.
For subscription authentication, follow Pullfrog's own login flow. Credentials
stay in Pullfrog's store, not this project's state or worker images.

Keep automatic reviews, automatic re-reviews and unwanted coding automations off.
Enable collaborator mentions. For review-only use set Pullfrog's code-push policy
to disabled. A newly generated workflow also sets `push: disabled`; an existing
workflow is preserved except for runner routing, so review its policy yourself.

Merge the draft workflow PR only after the diagnostic passes. If the installer
created a new workflow, use Pullfrog's **Verify manual installation** operation.
Then request `@pullfrog review this PR` on a test PR. Check that GitHub reports the
temporary self-hosted runner and that the Buildkite worker is released afterward.
This test uses the connected model subscription/API credentials.

## Container restrictions

The default keeps Docker's security profiles. The diagnostic checks whether the
container can create the user/PID/mount namespaces Pullfrog needs. Some hosts
block them even when Docker is installed.

`--relax-container-sandbox` explicitly selects `seccomp=unconfined` and
`apparmor=unconfined` for the runner container. This broadens the container's
kernel access; it is not a universal compatibility or isolation guarantee. Use
an appropriately isolated worker environment. It never adds `--privileged`, a
host Docker socket, host directories, host networking or Buildkite credentials.
Host kernel settings can still block namespaces. Custom, narrowly tailored
profiles are a future improvement; the installer does not alter host settings.

The bundled image downloads the official x64 runner and verifies its SHA-256.
It builds on demand on each Docker daemon and is cached there. The Ubuntu base
tag and package repositories are mutable; this is not a fully reproducible image.
For controlled deployments, build, audit and publish an image, then use its digest.
Keep the runner current with GitHub's update policy; update the plugin/image and
rerun diagnostics when upgrading.

## Resume and recover

Setup state defaults to `$XDG_STATE_HOME/buildkite-runner-bridge/setup-state.json`
or `~/.local/state/buildkite-runner-bridge/setup-state.json`, with mode 0600.
**It contains the App private key and webhook secret.** Keep it outside source
repositories; back it up securely if you need resumability. Never attach it to
issues or diagnostics. No secrets are printed by the installer.

Rerun the same command/state file to reuse the pipeline, trigger, secret and PR.
If App registration was interrupted before credentials were saved, inspect the
named App in GitHub before clearing the state's registration-started marker.
If the trigger POST response was lost, its one-time endpoint URL may be
unrecoverable: inspect/remove only that incomplete trigger before resuming.
The installer refuses resource-name collisions with unmanaged objects.

For execution recovery, start **New Build** on the generated pipeline's trusted
default branch. No webhook metadata means recovery mode. A qualifying new review
event also finds previously stranded jobs in the same repository. No schedule is
installed. A lost initial event remains waiting until one of these happens.

Provisioning errors are retried at most twice. A completed/failed review is never
rerun automatically. After a hard worker crash, Docker cleanup code cannot run;
inspect the affected host and remove only confirmed `bkgh-` containers/registrations
owned by this installation. Recovery does not scan or delete arbitrary containers.

## Remove the integration

Disable the incoming trigger, finish/cancel active builds, and restore the
workflow's runner routing before removing the pipeline, App installation and
bridge secret. Remove the App registration and local state when no installation
needs them. Do not uninstall Pullfrog if other workflows still use it.

## Primary references

- [GitHub App manifest registration](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest)
- [Buildkite pipeline triggers](https://buildkite.com/docs/apis/webhooks/incoming/pipeline-triggers)
- [Buildkite Secrets API](https://buildkite.com/docs/apis/rest-api/clusters/secrets)
- [GitHub ephemeral runners](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
- [Pullfrog setup](https://docs.pullfrog.com/getting-started)
- [Pullfrog self-hosted runner requirements](https://docs.pullfrog.com/security#self-hosted-runners)
- [Pullfrog subscription authentication](https://docs.pullfrog.com/codex-auth)
