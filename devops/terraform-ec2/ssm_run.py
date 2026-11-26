"""
ssm_run.py: run a shell script on the demo instance through SSM Run Command
(document AWS-RunShellScript) and print its output. No SSH, no open ports.

The exact script, the command ID, status and output are appended to a log
file. Account IDs, IPv4 addresses and ip-x-x-x-x hostnames are redacted.

Usage:
  python ssm_run.py --instance i-... --log ../../results/aws/c2_x.log --timeout 900 -- 'bash commands'
  python ssm_run.py --instance i-... --log out.log --file script.sh
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time

REGION = "ap-southeast-2"


def redact(text: str) -> str:
    text = re.sub(r"\b\d{12}\b", "<ACCOUNT-ID>", text)
    text = re.sub(r"ip-\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}", "ip-<REDACTED>", text)
    text = re.sub(r"\b(?!0\.0\.0\.0\b|127\.0\.0\.1\b)(\d{1,3}\.){3}\d{1,3}\b", "<IP>", text)
    return text


def aws(*args: str) -> dict:
    out = subprocess.run(["aws", *args, "--region", REGION, "--output", "json"], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(redact(out.stderr.strip()))
    return json.loads(out.stdout) if out.stdout.strip() else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--timeout", type=int, default=600, help="seconds (max 172800)")
    ap.add_argument("--file")
    ap.add_argument("script", nargs="*")
    a = ap.parse_args()
    script = open(a.file, encoding="utf-8").read() if a.file else " ".join(a.script)

    params = json.dumps({"commands": [script], "executionTimeout": [str(a.timeout)]})
    sent = aws(
        "ssm",
        "send-command",
        "--instance-ids",
        a.instance,
        "--document-name",
        "AWS-RunShellScript",
        "--comment",
        "telematics-demo",
        "--parameters",
        params,
        "--timeout-seconds",
        "60",
    )
    cid = sent["Command"]["CommandId"]
    start = time.time()
    while True:
        time.sleep(3)
        try:
            inv = aws("ssm", "get-command-invocation", "--command-id", cid, "--instance-id", a.instance)
        except RuntimeError as e:
            if "InvocationDoesNotExist" in str(e):
                continue
            raise
        if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
            break
        if time.time() - start > a.timeout + 120:
            inv = {"Status": "LocalTimeout", "StandardOutputContent": "", "StandardErrorContent": ""}
            break

    out = redact(inv.get("StandardOutputContent", ""))
    err = redact(inv.get("StandardErrorContent", ""))
    with open(a.log, "a", encoding="utf-8") as f:
        f.write(f"\n### {dt.datetime.now(dt.timezone.utc).isoformat()} SSM Run Command {cid} on {a.instance}\n")
        f.write("$ " + script.replace("\n", "\n  ") + "\n")
        f.write(f"--- status: {inv['Status']} (exit {inv.get('ResponseCode')})\n--- stdout:\n{out}\n")
        if err.strip():
            f.write(f"--- stderr:\n{err}\n")
    print(f"[{inv['Status']} exit={inv.get('ResponseCode')}]")
    print(out)
    if err.strip():
        print("--- stderr:\n" + err)
    return 0 if inv["Status"] == "Success" else 1


if __name__ == "__main__":
    sys.exit(main())
