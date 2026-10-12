# Architecture

## Control and execution

GitHub owns workflow execution, job assignment and OIDC. Buildkite owns worker
selection and per-job concurrency. The project connects these two schedulers.
It does not emulate GitHub Actions or mint GitHub OIDC itself.

The integration App's webhook targets a Buildkite incoming `github` trigger.
Buildkite verifies the signature before creating a build. The bootstrap reads
`buildkite:webhook`, accepts matching queued events, and fetches live GitHub job
and run objects. It accepts only the configured repository, workflow path,
`workflow_dispatch` event, trusted default branch, current attempt, queued status
and exact expected runner labels.

The bootstrap also enumerates active runs for stranded queued jobs, including
jobs inside an `in_progress` run. It uploads one worker step per candidate and
exits. Bootstrap and worker steps may execute on different pool members. No local
state must move between them; the generated steps carry non-secret configuration.

## Duplicate handling

Each worker step holds a Buildkite concurrency group derived from repository,
job ID and attempt for its entire lifetime. On acquiring that group, it checks
live GitHub status again. Duplicate delivery can enqueue multiple steps, but
later steps exit if the job is no longer queued. The registration name includes
an invocation-specific random suffix; cleanup only targets that exact name.

There is no separate lock database, no runtime Buildkite API token and no
exactly-once-delivery claim. A large event burst may create redundant short steps.
This is a low-volume initial integration, not a general autoscaling fleet manager.

## Runner lifecycle

The worker prepares its image, revalidates demand, obtains a temporary registration
token, and sends it through container stdin. The entrypoint registers with
`--ephemeral --no-default-labels` and then execs the official runner. The temporary
registration token is not in Docker arguments, environment, labels or a mounted
file. GitHub's normal runner credential files exist inside the disposable container.

Custom labels contain an integration label and a workflow run/attempt label.
Removing default labels prevents unrelated `runs-on: self-hosted` jobs from
accidentally acquiring the container. Labels are routing constraints, not a
security boundary against a user who can modify repository workflows. Only
enable the integration on repositories whose workflow authors you trust.

The host monitors the intended job at ten-second intervals **while it is active**.
It tolerates bounded transient API failures, applies startup/execution deadlines,
and cleans up on completion or signal. GitHub job conclusion, not runner process
exit alone, determines review success. Provisioning failures use exit 75, the
only configured automatic retry condition. Action failures use exit 1.

## Failure boundaries

- Missing initial webhook: next qualifying event or manual recovery is required.
- Webhook accepted but bootstrap fails: Buildkite exposes the failed build; retry
  it manually or use later activity recovery. No infinite background retry loop.
- Missing filter support: explicit `--no-filter` permits short no-op builds for
  irrelevant App events; the plugin still validates before provisioning.
- GitHub outage during a run: bounded monitoring retries, then fail and clean up.
- Hard worker loss: in-process cleanup cannot execute; operator/host lifecycle
  cleanup is required. No coordinator exists to reach offline hosts.
- Running GitHub job fails: report it, do not dispatch a new review automatically.
- Secret/authentication failure: fail visibly. Do not bypass GitHub OIDC or use
  another account's model credentials.

## Installer state

The one-time CLI uses GitHub CLI authentication for repository changes and a
Buildkite token for provisioning. A temporary loopback web server implements the
GitHub manifest browser round trip. The App/webhook private state is written
atomically to a 0600 local file; resource IDs support resumption. Pipeline and
secret ownership markers prevent modifying unrelated resources with matching names.

Remote writes are not blindly retried after an ambiguous response. Subsequent
runs reconcile existing resources. One-time credentials that were lost before
checkpointing require operator intervention, documented in INSTALL.md.
