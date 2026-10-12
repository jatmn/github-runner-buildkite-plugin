# Agent instructions

These instructions apply throughout this repository. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before editing and use its validation and PR
requirements. Read [docs/PLAN.md](docs/PLAN.md) for accepted scope and outstanding
release gates, and [SECURITY.md](SECURITY.md) before touching trust boundaries.

## Purpose and scope

This project runs ephemeral GitHub Actions runners inside jobs on an existing
Buildkite pool. Pullfrog's normal manual trigger is the initial integration.
The installer provisions customer-owned integration resources; it is not a
project-hosted service. The implementation is experimental, with live acceptance
work still outstanding.

Preserve these contracts:

- GitHub owns Actions job assignment and OIDC. Do not emulate Actions or mint
  substitute OIDC credentials.
- Buildkite selects any eligible pool worker. Bootstrap and worker steps can run
  on different hosts; do not depend on shared local files between steps.
- Recovery runs on relevant activity or an explicit manual build. Do not add a
  persistent coordinator, idle polling, a custom review command, or automatic
  reruns of failed reviews.
- A lost initial webhook requires later activity or manual recovery. Keep that
  limitation visible rather than claiming guaranteed delivery.
- Pullfrog owns model authentication. This project must not collect, copy, or
  repurpose model subscription credentials.
- Initial support is one repository per installation, GitHub.com, Linux x64,
  Docker, clustered Buildkite agents, and Buildkite Secrets. New platform claims
  require the plan changes and acceptance evidence described in CONTRIBUTING.md.

## Source map

| Path | Responsibility |
| --- | --- |
| `runner_bridge/cli.py` | CLI arguments and command dispatch |
| `runner_bridge/setup.py` | Guided setup, App manifest callback, resource reconciliation and private state |
| `runner_bridge/api.py` | HTTP clients, bounded read retries and App authentication |
| `runner_bridge/config.py` | Configuration, identities, labels and generated Buildkite steps |
| `runner_bridge/plugin.py` | Live eligibility, recovery, runner supervision and cleanup |
| `hooks/` and `plugin.yml` | Buildkite plugin interface and checkout behavior |
| `runner/` | Docker image and ephemeral runner registration entrypoint |
| `tests/test_bridge.py` | Regression tests, API fixtures and local boundary checks |
| `docs/` | Installation, architecture, durable plan and validation evidence |

Trace a change through its callers and these boundaries before editing. For
configuration changes, check CLI options, serialization, plugin schema, generated
steps, installer output, tests, and docs for affected consumers; update only the
surfaces the change actually requires.

## Working on a change

1. Inspect the current branch, diff, applicable instructions, and related work.
   Preserve unrelated local changes. Define the requested observable outcome
   before choosing an implementation.
2. Reproduce bugs when practical. Distinguish an application failure from a
   missing dependency, service outage, or permission failure.
3. Make the smallest coherent change. Keep Python 3.11 compatibility and prefer
   existing standard-library and Bash code paths. Follow CONTRIBUTING.md before
   introducing dependencies, languages, or architectural changes.
4. Review every changed hunk and affected contract. Avoid unrelated cleanup,
   broad rewrites, speculative abstractions, and changes made only to silence a
   failing check.
5. Validate the final revision and report what ran, what failed, and what remains
   unverified. Never describe mocked service behavior as a successful deployment.

## Security and lifecycle boundaries

- Treat webhook payloads as input, not authorization. Preserve live GitHub checks
  for the configured repository, workflow, event, branch, run attempt, job state,
  and exact expected labels before provisioning. Revalidate after waiting or
  slow image preparation.
- Preserve the per-job/run-attempt Buildkite concurrency group for the worker's
  lifetime. Duplicate deliveries may enqueue redundant steps; do not claim
  exactly-once execution from scheduling tests.
- Keep runner registration ephemeral and omit default labels. Custom labels
  prevent accidental assignment; they do not isolate hostile workflow authors.
- Do not check out PR code on the Buildkite host or mount the host workspace,
  Docker socket, agent credentials, or arbitrary host environment into the
  runner. Long-lived App keys stay outside the container; send temporary
  registration tokens through stdin, not command arguments or environment.
- Preserve explicit opt-in for relaxed Docker security profiles. Do not silently
  enable privileged mode or weaken isolation to make a diagnostic pass.
- Preserve API origin restrictions and redirect rejection for authenticated
  requests. Do not expose response bodies, tokens, PEM contents, webhook secrets,
  or private state in exceptions or logs.
- Reconcile ambiguous remote writes before trying again. Preserve resource
  ownership checks, atomic private-state writes, and restrictive permissions.
  Never overwrite an unrelated resource merely because its name matches.
- Bound startup, execution, monitoring retries, and shutdown. Preserve the
  distinction between infrastructure retries and failed review execution.
  GitHub job state determines the result; runner process exit alone is not proof
  of success.
- Clean up only resources owned by the current invocation, using exact recorded
  identities. Avoid broad container, runner, or process deletion. Report failed
  cleanup and retain the documented limitation that an offline worker cannot
  execute its own cleanup.

## Validation and evidence

Use the commands and change-specific requirements in
[CONTRIBUTING.md](CONTRIBUTING.md#validation). There is no additional agent-only
test suite. Documentation-only changes need whitespace checks, valid links, and
verification that technical claims match the code; they do not require an
application test run.

For behavior changes, prefer a regression test that fails for the reported
reason before the fix and passes afterward. Exercise observable outcomes through
the relevant production path. Assert meaningful routing, payload, state, or
cleanup behavior rather than merely asserting that a mock was called. Include
failure or cancellation cases when the change affects them. Do not weaken
assertions or duplicate implementation logic to make tests pass.

Keep tests isolated from real accounts. Use fictional fixtures, temporary paths,
and scoped environment patches that restore state. Track and stop processes or
containers created for validation. Distinguish fixture tests, local OpenSSL or
container diagnostics, and live integration evidence in the report.

Do not run setup mutations, connect a pool, register a runner, or use model
credentials merely to validate a code change. Such operations need explicit task
authorization; existing credentials are not permission. Read-only preflight can
still contact services, so describe it accurately. Keep public content free of
private deployment details, personal information, secrets, and machine paths.

## Delivery

Follow the repository's PR workflow. Do not push to `main`, bypass protection,
merge, publish a release, or deploy unless the task explicitly authorizes that
action. Keep the PR title and description aligned with its final scope. Include
the problem, resulting behavior, validation, and material limitations. Mention
missing or pending checks rather than inventing a passing CI result.

Update the durable plan when an approved scope decision changes it, and record
new validation evidence where appropriate. Preserve the experimental status
until the documented release acceptance gates have actually passed.
