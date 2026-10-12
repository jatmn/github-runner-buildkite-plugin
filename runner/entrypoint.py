"""Read bootstrap credentials from stdin; no secret env vars or mounted files."""
import json
import os
import subprocess
import sys

config = json.loads(sys.stdin.readline())
args = ["./config.sh", "--unattended", "--ephemeral", "--no-default-labels", "--url", config["url"],
        "--token", config["token"], "--name", config["name"], "--labels", ",".join(config["labels"]),
        "--work", "_work"]
# Registration output is suppressed because it can contain URLs/credentials.
result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if result.returncode:
    sys.exit(75)
config.clear()
args.clear()
os.execv("./run.sh", ["./run.sh"])
