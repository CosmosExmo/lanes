"""State: inventory cache, ledger, job dirs, config, redaction. Stdlib only."""
import contextlib
import fcntl
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

DEFAULT_CONFIG = {"cursor_read_quota": False, "agy_allow_shell": False,
                  "orchestrator_reserve_pct": 3, "prompt_hint": True, "agent_guard": False}


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


@contextlib.contextmanager
def _flock(name):
    d = CACHE_DIR()
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(d / name, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def inventory_lock():
    """Exclusive lock around an inventory read-modify-write (discover's write, the runner's exhaustion mark)."""
    return _flock("inventory.lock")


def reserve_lock():
    """Exclusive lock held from a job's plan-limit check until its meta (with est_cost) is written, so two
    starts on one overage pool cannot both pass the check. Separate from inventory_lock: the check reads quota."""
    return _flock("reserve.lock")


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


def _write_row(row: dict) -> None:
    d = CACHE_DIR()
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(d / "ledger.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)  # an older ledger may be world-readable
        os.write(fd, (json.dumps(row) + "\n").encode())
    finally:
        os.close(fd)


def append_ledger(row: dict) -> None:
    _write_row({"ts": time.time(), **row})


def read_ledger(since=None) -> list:
    """One merged dict per job (file order, later keys win), ordered by first appearance."""
    try:
        lines = (CACHE_DIR() / "ledger.jsonl").read_text().splitlines()
    except OSError:
        return []
    merged = {}
    for n, l in enumerate(lines):
        try:
            row = json.loads(l)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        merged.setdefault(row.get("job", ("_row", n)), {}).update(row)
    rows = list(merged.values())
    if since is not None:
        rows = [r for r in rows if r.get("ts", 0) >= since]
    return rows


def update_ledger(job_id: str, **fields) -> None:
    """Append-only update row (no ts, so it never overrides the original's); read_ledger merges."""
    _write_row({"job": job_id, **fields})


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def job_dir(job_id: str) -> Path:
    p = CACHE_DIR() / "jobs" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


_REDACTIONS = [
    (re.compile(r"eyJ[\w-]*\.[\w-]+\.[\w-]+"), "<redacted>"),
    (re.compile(r"Bearer\s+\S+", re.I), "<redacted>"),
    (re.compile(r"\bsk-[\w-]+"), "<redacted>"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
]


def redact(text: str) -> str:
    for rx, sub in _REDACTIONS:
        text = rx.sub(sub, text)
    return text
