import argparse
import os
from pathlib import Path
import sys

from . import __version__
from .api import BridgeError


def parser():
    root = argparse.ArgumentParser(description="Real GitHub Actions runners on an existing Buildkite pool")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Guided account and pipeline setup")
    for option in ("repo", "buildkite-org", "cluster", "queue", "source", "revision", "image"):
        setup.add_argument("--" + option)
    setup.add_argument("--state", default=str(Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "buildkite-runner-bridge/setup-state.json"))
    setup.add_argument("--plan", action="store_true", help="Read-only preflight; no remote mutations")
    setup.add_argument("--yes", action="store_true", help="Accept the displayed resource setup; browser consent still required")
    setup.add_argument("--no-filter", action="store_true", help="Allow no-op builds when Buildkite trigger filtering is unavailable")
    setup.add_argument("--relax-container-sandbox", action="store_true", help="Explicitly disable Docker seccomp/AppArmor profiles for runner user namespaces")
    commands.add_parser("plugin", help="Internal Buildkite hook entry point")
    return root


def main():
    args = parser().parse_args()
    try:
        if args.command == "setup":
            from .setup import setup
            setup(args)
            return 0
        from .plugin import plugin_main
        return plugin_main()
    except (BridgeError, ValueError, KeyError, TypeError) as exc:
        # Validation exceptions can contain inputs; only domain errors are safe.
        message = str(exc) if isinstance(exc, BridgeError) else "Invalid configuration or unexpected service response"
        print("Error: " + message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; rerun setup to resume", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
