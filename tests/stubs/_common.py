"""Shared helpers for the CLI stubs; behaviour is chosen by env STUB_MODE (comma list)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMAIL = "someone@example.com"


NAME = ""


def log(name):
    global NAME
    NAME = name
    p = os.environ.get("STUB_LOG")
    if p:
        with open(p, "a") as f:
            f.write(json.dumps([name] + sys.argv[1:]) + "\n")


def has(flag):
    return flag in os.environ.get("STUB_MODE", "").split(",")


def fixture(name):
    return (ROOT / "tests" / "fixtures" / name).read_text()


def show_help(key):
    if has("nohelp"):
        print("usage: stub [options]")
        return
    flags = json.load(open(ROOT / "adapters.json"))[key]["required_flags"]
    print("Options:\n" + "\n".join("  " + f for f in flags))


def model_call(out_path=None):
    """Pretend to send the probe prompt (or a runner job) to a model."""
    if has("limit"):  # print this CLI's first limit pattern, like a real plan error
        key = {"cursor-agent": "cursor"}.get(NAME, NAME)
        sys.stderr.write(json.load(open(ROOT / "adapters.json"))[key]["limit_error_patterns"][0] + "\n")
        sys.exit(1)
    if has("write") or has("write2"):
        Path("stub_output.txt").write_text("changed again" if has("write2") else "changed")
    if has("write") or has("write2") or has("nowrite") or has("json"):
        if out_path:
            Path(out_path).write_text("ANSWER")
        print(json.dumps({"result": "ANSWER", "response": "ANSWER"}) if has("json") else "done")
        return
    if has("ok"):
        if out_path:
            Path(out_path).write_text("OK")
        print("OK")
        return
    sys.stderr.write("Error: code=QUOTA_EXHAUSTED rate_limit_reached usage_limit_reached\n" if has("plan_error")
                     else "Error: something broke\n")
    sys.exit(1)
