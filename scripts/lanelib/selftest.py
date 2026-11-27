"""selftest / install / update. Stdlib only."""
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import discover, state

SKILL = "lanes"
_VER = re.compile(r"\d+(?:\.\d+)+")


def _version(out):
    out = out.strip().splitlines()[0] if out.strip() else ""
    m = _VER.search(out)
    return m.group(0) if m else out


def _row(cli, a, installed, version="", missing=(), live_ok=None, status="ok"):
    return {"cli": cli, "installed": installed, "version": version, "verified_version": a.get("verified_version"),
            "flags_missing": list(missing), "live_ok": live_ok, "status": status}


def _live_pool(inv, cli):
    """A subscription pool reachable through `cli` (any access entry), working pools first."""
    ps = [p for p in inv.values() if p["billing"] == "subscription"
          and any(x["cli"] == cli for x in p.get("access") or [{"cli": p["cli"]}])]
    return min(ps, key=lambda p: p["status"] not in ("usable", "quota_unknown"), default=None)


def selftest(adapters: dict, live: bool) -> list:
    inv = (state.read_inventory(None) or {}).get("pools") or {}
    rows = []
    for cli, a in adapters.items():
        if shutil.which(a["bin"]) is None:
            rows.append(_row(cli, a, False, status="not_installed"))
            continue
        ver = discover.run_cmd(a["version_cmd"], 30)
        version = _version(ver.stdout + ver.stderr)
        h = discover.run_cmd(a["help_cmd"], 30)
        text = h.stdout + h.stderr
        missing = [f for f in a["required_flags"] if f not in text]
        live_ok, status = None, "ok"
        if missing:
            status = "flags_missing"
        elif live and a.get("verified", True):  # an unverified adapter is never worth a live call
            pool = _live_pool(inv, cli)
            if pool is None:
                status = "no_pool"
            else:
                live_ok = discover.probe(adapters, discover.Pool.from_dict(pool), cli)
                status = "ok" if live_ok else "live_failed"
        if status == "ok" and a.get("verified_version") and a["verified_version"] not in (ver.stdout + ver.stderr) \
                and not live:
            status = "version_changed"  # flags present; run --live to confirm the new version works
        rows.append(_row(cli, a, True, version, missing, live_ok, status))
    return rows


def failed(rows, adapters):
    return any(r["status"] in ("flags_missing", "live_failed") and adapters[r["cli"]].get("verified", True)
               for r in rows)


def _link(target: Path, src: Path, repo: Path, dry):
    """Symlink target -> src. Never touches a non-symlink; replaces only our own or broken links."""
    if target.is_symlink():
        if target.resolve() == src.resolve():
            return None
        if target.exists() and repo not in target.resolve().parents and target.resolve() != repo:
            return f"skip {target} (foreign symlink)"
        if not dry:
            target.unlink()
    elif target.exists():
        return f"skip {target} (not a symlink)"
    if not dry:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(src)
    return f"link {target} -> {src}"


def install(repo: Path, bin_only=False, dry=False) -> list:
    repo = Path(repo).resolve()
    home = Path.home()
    script = repo / "scripts" / "lanes.py"
    if not dry:
        script.chmod(script.stat().st_mode | 0o111)
    acts = [_link(home / ".local" / "bin" / "lanes", script, repo, dry)]
    if not bin_only:
        dirs = [home / ".agents/skills", home / ".claude/skills", home / ".codex/skills",
                home / ".gemini/antigravity-cli/skills", home / ".cursor/skills"]
        if os.environ.get("CLAUDE_CONFIG_DIR"):
            dirs.append(Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() / "skills")
        seen = set()
        for d in dirs:
            if d.is_dir() and d.resolve() not in seen:
                seen.add(d.resolve())
                acts.append(_link(d / SKILL, repo, repo, dry))
    return [state.redact(x) for x in acts if x]


def _vtuple(v):
    return tuple(int(x) for x in v.split(".")) if _VER.fullmatch(v or "") else None


def changes_since(changelog: str, old: str) -> str:
    """CHANGELOG sections (`## X.Y.Z ...`) newer than `old`; "" if none or `old` is unreadable."""
    base = _vtuple(old)
    if base is None:
        return ""
    keep = []
    for sec in re.split(r"(?m)^(?=## )", changelog):
        m = re.match(r"## (\d+(?:\.\d+)+)", sec)
        if m and _vtuple(m.group(1)) > base:
            keep.append(sec.rstrip())
    return "\n\n".join(keep)


def update(repo: Path) -> int:
    import sys

    def version():
        try:
            return (repo / "VERSION").read_text().strip()
        except OSError:
            return ""
    old = version()
    try:
        r = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"], capture_output=True, text=True)
    except OSError:
        sys.stderr.write("lanes update needs git, which was not found on PATH\n")
        return 2
    if r.returncode:
        sys.stderr.write(state.redact(r.stdout + r.stderr))
        return 2
    new = version()
    if new == old:
        print(f"lanes is up to date ({new})")
        return 0
    print(f"lanes updated {old or '?'} -> {new}. What changed:\n")
    try:
        print(changes_since((repo / "CHANGELOG.md").read_text(), old) or "(see CHANGELOG.md)")
    except OSError:
        print("(see CHANGELOG.md)")
    print()
    return 0
