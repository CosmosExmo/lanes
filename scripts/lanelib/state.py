"""State: inventory cache, ledger, job dirs, config, redaction. Stdlib only."""
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

DEFAULT_CONFIG = {"cursor_read_quota": False, "agy_allow_shell": False,
                  "orchestrator_reserve_pct": 3}


def CACHE_DIR() -> Path:
    return Path(os.environ.get("LANES_CACHE") or "~/.cache/lanes").expanduser()


def CONFIG_PATH() -> Path:
    return Path(os.environ.get("LANES_CONFIG") or "~/.config/lanes/config.json").expanduser()


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH().read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def read_inventory(max_age_s):
    p = CACHE_DIR() / "inventory.json"
    try:
        if max_age_s is not None and time.time() - p.stat().st_mtime > max_age_s:
            return None
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def write_inventory(inv: dict) -> None:
    d = CACHE_DIR()
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".inventory-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(inv, f)
        os.replace(tmp, d / "inventory.json")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_ledger(row: dict) -> None:
    d = CACHE_DIR()
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(d / "ledger.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(row) + "\n").encode())
    finally:
        os.close(fd)


def read_ledger(since=None) -> list:
    try:
        lines = (CACHE_DIR() / "ledger.jsonl").read_text().splitlines()
    except OSError:
        return []
    rows = []
    for l in lines:
        try:
            rows.append(json.loads(l))
        except ValueError:
            continue
    if since is not None:
        rows = [r for r in rows if r.get("ts", 0) >= since]
    return rows


def update_ledger(job_id: str, **fields) -> None:
    """Append an update row; readers see the last row per job (append-only, race-free)."""
    append_ledger({"job": job_id, **fields})


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def job_dir(job_id: str) -> Path:
    p = CACHE_DIR() / "jobs" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


_REDACTIONS = [
    (re.compile(r"eyJ[\w-]*\.[\w-]+\.[\w-]+"), "<redacted>"),
    (re.compile(r"Bearer\s+\S+"), "<redacted>"),
    (re.compile(r"\bsk-[\w-]+"), "<redacted>"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
]


def redact(text: str) -> str:
    for rx, sub in _REDACTIONS:
        text = rx.sub(sub, text)
    return text
