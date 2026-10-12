# Contributing

Contributions are welcome. This project has a deliberately narrow scope and a
limited maintenance budget. Acceptance depends on whether a change supports the
project's direction, can be validated, and remains practical to maintain. A
useful idea may still be declined or deferred.

## Before starting

1. Search issues and open or recently closed pull requests for related work.
   Check for duplicates before starting another implementation.
2. Read [README.md](README.md), the [project plan](docs/PLAN.md), and the
   [architecture](docs/ARCHITECTURE.md). For setup or runner changes, also read
   the [installation guide](docs/INSTALL.md) and [security model](SECURITY.md).
3. Discuss changes to architecture, supported platforms, credentials, or project
   scope in an issue before implementing them. Include the problem and evidence
   for the proposed approach; do not include private deployment details.
4. Keep each PR focused on one problem. Separate unrelated cleanup from fixes
   and features.

Overlapping PRs may be consolidated or closed to keep review in one place.

## Project direction

Contributions should improve the existing integration: installer reliability,
runner lifecycle and cleanup, routing, documentation, or meaningful regression
coverage. Reducing maintenance work without widening the supported surface is
also useful.

Preserve these design decisions:

- Use available workers in an existing Buildkite pool, without assuming a fixed
  host.
- Run real GitHub Actions runners so GitHub remains responsible for Actions
  execution and OIDC.
- Use Pullfrog's normal manual trigger and leave model authentication to
  Pullfrog.
- Keep setup guided and customer-owned, with no separate always-on coordinator
  or polling while idle.
- Keep recovery bounded. Document the need for later activity or manual recovery
  when the initial webhook is completely lost.

Changes that introduce a service, polling, a new credential boundary, additional
platform support, or automatic reviews require an agreed update to
[docs/PLAN.md](docs/PLAN.md) and its acceptance gates before implementation.
Broad refactors, speculative abstractions, and features better handled by the
upstream products may be declined.

## Implementation tools

The installer and runtime use Python 3.11+ and the standard library, with Bash
plugin hooks, a Docker runner image, YAML configuration, and Markdown docs.
Prefer those existing tools. Discuss new implementation languages, runtime
dependencies, and build systems before adding them; avoid introducing machinery
for a task the current tools already handle.

## Pull request expectations

Open a branch or fork and submit a PR against `main`. Only the repository owner
merges changes to `main`; direct pushes, force pushes, and deletion are blocked
by repository rules. Resolve review conversations before merging. Maintainer
acceptance is required even when the available checks pass.

Include the following in the PR description:

- The problem, resulting behavior, and why the change belongs in this project.
- Related issues or discussion, when available, and confirmation that you checked
  for overlapping PRs.
- Validation commands and results for the submitted revision, including failures
  or checks you could not run.
- Any changes to supported behavior, credential handling, deployment requirements,
  or documented limitations.

Update affected documentation and add regression coverage when behavior changes.
Never include real credentials, private account or repository examples, local
machine paths, environment dumps, installer state, or private logs in a diff,
fixture, screenshot, or PR description. Use fictional data in public examples.

## Validation

For Python or hook changes, run these commands from the repository root before
committing or updating the PR:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q runner_bridge
bash -n hooks/checkout hooks/command
```

For every change, check whitespace:

```bash
git diff --check
```

For documentation-only changes, also verify relative links and check that
commands, examples, and claims match the implementation. Application tests are
not required solely for prose edits.

Tests should exercise observable behavior. Routing, credentials, API mutations,
concurrency, and cleanup changes need meaningful coverage of their affected
boundaries. Identify fixtures and mocked services in the validation report.
Passing fixture tests does not establish live Buildkite or Pullfrog compatibility.

For runner image changes, build on a Linux x64 Docker host and run the
namespace/sudo diagnostic documented in the [installation guide](docs/INSTALL.md).
Report host and image versions, security profile settings, and untested behavior.

Use the [release acceptance gates](docs/PLAN.md#release-acceptance-gates) when
making live compatibility or release-readiness claims. Record what actually ran
and preserve the distinction between local checks and live integration evidence
in [docs/VALIDATION.md](docs/VALIDATION.md). The project remains experimental
until those acceptance gates are satisfied.

Live tests require infrastructure you are authorized to use. Model execution
requires the operator's explicit consent. Do not connect someone else's pool or
repository, incur model usage, or consume existing credentials implicitly.

## Review and follow-up

Re-run affected checks after each revision and before requesting another review.
Keep any configured CI checks passing, and explain external or environmental
failures rather than hiding them. Address routine formatting and validation
failures before asking maintainers to review again.

Stay available to answer questions and revise the PR. If you cannot continue,
leave a short update so the maintainer can decide how to proceed. PRs with no
response for one week after review feedback may be closed as abandoned. You can
later ask to reopen the PR or submit a fresh one with the earlier work linked.

## Security reports

Follow [SECURITY.md](SECURITY.md) for private vulnerability reporting. Do not post
vulnerability details, exploit payloads, credentials, or private deployment data
in public issues or pull requests.
