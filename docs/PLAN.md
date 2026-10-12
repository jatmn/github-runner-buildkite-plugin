# Project plan and durable scope

## Goals and objectives

| ID | Required outcome | Current implementation |
| --- | --- | --- |
| G1 | Reuse an existing Buildkite pool; no fixed worker assumption | Queue-targeted bootstrap and worker steps |
| G2 | Execute real GitHub Actions jobs, preserving GitHub OIDC | Ephemeral runner container, not an Actions emulator |
| G3 | Start with Pullfrog's normal manual trigger | Standard workflow_dispatch workflow and run-specific labels |
| G4 | One guided setup command | App manifest browser flow, Buildkite pipeline/trigger/secret APIs, workflow PR |
| G5 | No separate always-on coordinator or idle polling | Webhook dispatch, activity recovery, manual New Build recovery |
| G6 | Bounded infrastructure retries and duplicate protection | Per-job concurrency groups, live revalidation, exit-code-specific retries |
| G7 | Portable public project without private or personal configuration | Generic fixtures/examples; account values supplied at installation |
| G8 | Truthful verification and documented limitations | Local tests plus explicit live acceptance gates below |

## Decisions to preserve

- GitHub remains the Actions control plane. Buildkite supplies compute only.
- The customer owns the integration GitHub App. Its webhook points directly to
  a signed Buildkite incoming GitHub trigger. No project-hosted backend.
- Pullfrog remains responsible for prompting, model authentication and reviews.
  Subscription credentials stay in Pullfrog's supported store.
- No special `/review` parser. No automatic reviews or automatic retries of
  failed/completed reviews. Users configure Pullfrog's manual behavior.
- Runtime coordination uses Buildkite concurrency groups and GitHub job state.
  Redundant Buildkite steps are acceptable; exactly-once webhook delivery is not
  promised. An extra runtime Buildkite API token/database is unnecessary in v1.
- A totally missed initial event requires later activity or manual recovery.
  Do not quietly add polling to conceal this limitation.
- Initial support is one repository per installation, GitHub.com, Linux x64,
  Docker, Buildkite clustered agents and Buildkite Secrets. Additional platforms
  need their own acceptance evidence.

## Delivered source components

- `runner_bridge/setup.py`: resumable installer and temporary local browser flow.
- `runner_bridge/plugin.py`: dispatch, recovery, runner supervision, diagnostics.
- `hooks/` and `plugin.yml`: Buildkite plugin entry points and configuration.
- `runner/`: Docker recipe and ephemeral registration entrypoint.
- `tests/`: isolated regression and API contract fixtures.
- Documentation: install, architecture, security and this plan.

## Release acceptance gates

- [ ] Complete App manifest registration from a local browser and install on a
  disposable repository; verify the signing secret reaches Buildkite correctly.
- [ ] Validate Buildkite trigger filtering on an enabled organization, and the
  explicitly selected no-filter fallback on an organization without filtering.
- [ ] Verify actual Buildkite plugin option names, dynamic upload, secret policy,
  queue routing and cross-build concurrency against a live test organization.
- [ ] Run the image diagnostic on a supported x64 host with documented namespace
  settings. Verify sudo and Pullfrog's shell/credential isolation.
- [ ] Prove one normal manual Pullfrog request uses pool compute and GitHub OIDC,
  authenticates via the configured provider, posts its review and cleans up.
- [ ] Repeat with duplicate delivery, missed delivery/manual recovery, failed
  registration, queued cancellation, running cancellation and lost worker.
- [ ] Confirm setup interruption/resumption at each remote mutation boundary;
  preserve customer edits on the generated workflow branch.
- [ ] Build/install the Python distribution and verify the published plugin
  source, image recipe and documentation match the tested revision.

Until these pass, label the project experimental. Local fixture tests are not a
substitute for these gates. Testing model execution needs the operator's explicit
consent; creating this project does not deploy it to any existing private repo.

## Follow-up work

1. Complete the isolated live acceptance campaign and record versions/results.
2. Publish a reproducible runner image by digest and an immutable release tag.
3. Add Buildkite device authorization after registering a public OAuth client.
4. Add supported external secret-store adapters if users need them.
5. Consider multiple repositories, ARM64 and additional Actions presets only
   after each has an end-to-end test. Generic architecture is not universal support.

## Explicit non-goals

SaaS hosting, always-on services, background polling schedules, emulated GitHub
OIDC, model credential sharing, automatic merges, arbitrary untrusted workflows,
Windows/macOS/GHES/Kubernetes support in the first version, and changes to existing
customer worker infrastructure during installation.

## Scope changes

Any change that introduces a service, polling, a new credential boundary, new
platform support or automatic review behavior must update this document and its
acceptance gates before implementation.
