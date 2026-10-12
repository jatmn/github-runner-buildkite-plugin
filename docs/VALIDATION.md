# Validation status

This records the first implementation's local checks. It does not certify a
customer deployment or replace the live acceptance gates in PLAN.md.

## Executed

- Python unittest suite: 26 passing tests, including a real loopback HTTP callback
  round trip with invalid-state rejection, real OpenSSL RS256 signature verification,
  and execution of the runner entrypoint against fixture registration scripts.
- Python source compilation and Bash hook syntax checks passed.
- Python wheel built successfully. No third-party runtime dependencies.
- Bundled Linux x64 Docker image built successfully with the official runner
  archive's SHA-256 verification. Docker engine version: 29.3.0.
- Container passwordless sudo and user/PID/mount namespace diagnostic:
  default Docker profiles failed with `Operation not permitted`; explicitly
  relaxed seccomp/AppArmor profiles passed on the test host.
- Temporary diagnostic containers were removed after testing.

## Mocked or unverified boundaries

GitHub and Buildkite API responses, Docker lifecycle transitions in unit tests,
and GitHub job assignment are fixtures. The browser callback test uses local HTTP
and a fake manifest conversion response; it does not register a real GitHub App.
The Docker diagnostic does not register a runner or execute Pullfrog.

Still unverified: live App registration/installation, Buildkite trigger/filter
behavior, cluster secret access, dynamic worker routing and concurrency, GitHub
OIDC acceptance by Pullfrog, model authentication, posted reviews and cleanup under
real worker loss. No private customer repository or pool was connected for these
checks, and no model requests were issued.

Before promoting a release, execute the isolated end-to-end campaign in PLAN.md
and record exact tested plugin, runner and action revisions.
