# Contributing

Read docs/PLAN.md before changing architecture or scope. Preserve the no-coordinator,
no-idle-polling design and generic public examples.

For Python or hook changes, run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q runner_bridge
bash -n hooks/checkout hooks/command
```

Add meaningful regression coverage for routing, credentials, API mutations,
concurrency or cleanup changes. Identify mocked boundaries in test reports.
Never claim live Buildkite/Pullfrog compatibility from fixtures alone. Do not
test against someone else's infrastructure or use model credentials implicitly.

For runner image changes, build on an x64 Docker host and run the documented
namespace/sudo diagnostic. Report host/image versions and any untested boundary.
Do not commit private state, personal account examples, tokens, PEM files,
environment dumps or generated deployment configuration.
