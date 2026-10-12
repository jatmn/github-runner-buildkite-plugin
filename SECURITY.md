# Security model

This is an experimental infrastructure integration. Use trusted repository
workflow authors and an appropriately isolated worker pool. A Docker container
is not a substitute for VM isolation when running hostile code.

## Credentials

- Setup uses the local GitHub CLI session and an entered Buildkite token. It
  does not persist the Buildkite setup token or forward it to reviews.
- The integration App has repository Administration write and Actions read.
  Its PEM key is kept in Buildkite Secrets and private local installer state.
- Buildkite's signed incoming trigger verifies the App webhook. A generic unsigned
  webhook is not a supported replacement.
- Only the trusted plugin host obtains runner registration tokens. Containers
  receive them through stdin for bootstrap; long-lived App keys stay outside.
- No host workspace, Docker socket, agent credential or arbitrary host environment
  is mounted/passed into the runner container.
- Pullfrog handles its own provider credentials and GitHub OIDC. This project
  does not store ChatGPT/Codex/Cursor or other model subscription credentials.

## Trust assumptions

Buildkite administrators, agents, plugin source, Docker daemon and GitHub workflow
authors are trusted. A repository administrator who changes workflow routing can
target its runners. Unique labels reduce accidental cross-assignment but are not
an authorization system. Private repository visibility alone does not establish
that all workflows are trusted.

The plugin must be commit-pinned. Image bootstrap verifies the official runner
archive checksum. The bundled Ubuntu base/packages are not immutable; operators
can provide an audited image by digest. Custom images must implement the stdin
bootstrap contract. App signing uses local OpenSSL and mode-0600 temporary files.

The opt-in relaxed Docker security profiles increase kernel exposure to support
user namespaces on some hosts. They never enable privileged mode, but must be
reviewed as an infrastructure decision. See INSTALL.md.

## Reporting

Use this repository's private vulnerability reporting feature if it is enabled.
Otherwise, open an issue requesting a private reporting channel without including
credentials, exploit payloads or private repository data. Do not attach installer
state, environment dumps, private PEM files, webhook URLs/secrets, or raw private
webhook payloads. Public examples and tests must use fictional data.
