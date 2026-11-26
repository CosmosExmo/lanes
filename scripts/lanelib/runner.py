"""Runner: execute one lane headless (read-only, or writing in an isolated git worktree), in the
foreground or background, record a ledger row with measured quota deltas, and mark a pool exhausted
on a limit error. Stdlib only.

Job output files (prompt/argv/stdout/stderr/out/meta) are raw local work product, mode 0600; only
the ledger and lanes' own printed diagnostics are redacted.
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import discover, pick, state
from .quota import Window, effective_used

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parents[1]
PREAMBLE = ("You are a worker started by `lanes`. Do not delegate, spawn agents or call other agent CLIs. "
            "Work only inside the current directory. Finish with a short result summary.")
POLL = 2
QUOTA_READERS = ("claude_usage", "codex_rpc", "agy_usage")
_JID = re.compile(r"^[\w-]+$")
_AUTH = re.compile(r"not logged in|unauthori[sz]ed|authentication (?:failed|required)|invalid api key|"
                   r"log ?in required|please log ?in|\b401\b", re.I)


def load_adapters():
    return json.loads((ROOT / "adapters.json").read_text())


def _die(code, msg):
    sys.stderr.write(state.redact(msg) + "\n")
    raise SystemExit(code)


def _jid(x):
    if not isinstance(x, str) or not _JID.match(x):
        _die(2, "bad job id")
    return x


def _jobs():
    return state.CACHE_DIR() / "jobs"


def _put(path, text=""):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return path


def _save_meta(m):
    d = _jobs() / m["job"]
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".meta-")  # 0600, replaced atomically for wait/overlap readers
    with os.fdopen(fd, "w") as f:
        json.dump(m, f)
    os.replace(tmp, d / "meta.json")


def _load_meta(job_id):
    try:
        return json.loads((_jobs() / job_id / "meta.json").read_text())
    except (OSError, ValueError):
        return None


# ---- argv / env / classification -------------------------------------------------------------

def child_env(adapter, mode):
    """discover.child_env (keys stripped, adapter env and per-mode env applied) plus LANES_DEPTH=1."""
    return {**discover.child_env(adapter, mode), "LANES_DEPTH": "1"}


def build_argv(adapter, lane, mode, prompt_path, out_path, timeout_s, cwd=""):
    """-> (argv, stdin path). prompt_path holds PREAMBLE + the user's prompt. The adapter's
    nesting_guard is appended for every mode; stdin is the prompt file for `stdin: prompt`, else /dev/null."""
    run = adapter["run"]
    tpl = run.get(mode) or (run["write"] if mode == "write_shell" else None)  # write already allows shell there
    if tpl is None:
        _die(2, f"{lane['cli']} has no {mode} mode")
    argv = discover.fill_template(
        tpl, provider=lane.get("provider") or "", model=lane["model"], effort=lane.get("effort") or "",
        prompt=Path(prompt_path).read_text(), prompt_file=str(prompt_path), out=str(out_path),
        timeout=str(timeout_s), cwd=json.dumps(str(cwd))[1:-1])  # {cwd} sits inside a JSON string (agy --settings)
    argv += adapter.get("nesting_guard", [])
    return argv, (str(prompt_path) if adapter["stdin"] == "prompt" else "/dev/null")


def classify_failure(adapter, rc, stdout, stderr):
    """limit | auth | error | None. A zero exit is never a failure (answer text may quote error strings)."""
    if rc == 0:
        return None
    text = stdout + "\n" + stderr
    if discover.is_limit_error(adapter, text):
        return "limit"
    return "auth" if _AUTH.search(text) else "error"


# ---- quota, ledger, exhaustion -----------------------------------------------------------------

def _soonest_reset(pool, now):
    fut = [w["resets_at"] for w in pool.get("windows", []) if w["resets_at"] > now]
    return min(fut) if fut else 0


def _mark_exhausted(pool_id):
    with state.inventory_lock():
        inv = state.read_inventory(None)
        p = ((inv or {}).get("pools") or {}).get(pool_id)
        if p is None:
            return
        now = time.time()
        p["status"], p["exhausted_until"] = "exhausted", _soonest_reset(p, now) or now + 3600
        state.write_inventory(inv)


def _read_quota(m, adapters, cfg):
    """Windows for the job's pool when its official CLI has a quota reader, else None."""
    pool = ((state.read_inventory(None) or {}).get("pools") or {}).get(m["pool"]) or {}
    if adapters.get(pool.get("cli"), {}).get("quota") not in QUOTA_READERS:
        return None
    return discover.read_pool_windows(m["pool"], adapters, cfg, time.time())


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _overlapped(m, t1):
    """True when another job on the same pool ran at any time during this one."""
    for d in _jobs().glob("*/meta.json"):
        try:
            o = json.loads(d.read_text())
        except (OSError, ValueError):
            continue
        if o.get("job") == m["job"] or o.get("pool") != m["pool"] or o.get("started", 0) > t1:
            continue
        if o.get("status") == "running":
            if _alive(o.get("pid") or os.getpid()):
                return True
        elif o.get("finished", 0) > m["started"]:
            return True
    return False


def _base(m):
    return re.sub(r"-(low|medium|high|xhigh)$", "", m)  # an effort suffix is not part of the model


def _in_access(pool, lane):
    """True when (cli, provider[, model]) is an access entry the discovery recorded for the pool."""
    for x in pool.get("access") or []:
        if x["cli"] == lane["cli"] and (x.get("provider") or None) == (lane.get("provider") or None) \
                and (not x.get("models") or _base(lane["model"]) in {_base(m) for m in x["models"]}):
            return True
    return False


def _busy_cost(pool_id, me):
    """Sum of recorded est_cost per window over the OTHER live jobs on this pool."""
    busy = {}
    for f in _jobs().glob("*/meta.json"):
        try:
            o = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if o.get("job") == me or o.get("pool") != pool_id or o.get("status") != "running" \
                or not _alive(o.get("pid") or os.getpid()):
            continue
        for k, v in (o.get("est_cost") or {}).items():
            busy[k] = busy.get(k, 0) + v
    return busy


def _delta(before, after):
    b = {w.name: w.used_pct for w in before}
    d = {w.name: round(w.used_pct - b[w.name], 2) for w in after if w.name in b and w.used_pct >= b[w.name]}
    return d or None  # a window that reset in between has no measurable delta


def _finish(m, status, rc, delta=None, failure=None):
    now = time.time()
    m.update(status=status, rc=rc, finished=now, duration_s=round(now - m["started"], 1))
    if failure:
        m["failure"] = failure
    row = {k: m.get(k) for k in ("job", "pool", "cli", "model", "effort", "tier", "kind", "task", "mode",
                                 "duration_s", "rc", "status", "worktree")}
    if row["worktree"]:  # relative to the cache: no home path in the ledger
        row["worktree"] = os.path.relpath(row["worktree"], state.CACHE_DIR())
    if delta:
        row["delta"] = delta
    state.append_ledger({**row, "outcome": None})  # ledger first: a finished meta implies a ledger row
    _save_meta(m)


# ---- execution ---------------------------------------------------------------------------------

class GitFailed(RuntimeError):
    pass


def _snapshot(wt):
    """Fingerprint of a worktree: HEAD, status, tracked diff and the content of untracked files
    (`git diff HEAD` ignores those, and a resumed job usually edits what job 1 created)."""
    def g(*a):
        r = discover.run_cmd(["git", "-C", str(wt), *a], 60)
        if r.returncode:  # an unreadable worktree must never look like "nothing changed"
            raise GitFailed("git status failed")
        return r.stdout
    h = hashlib.sha1()
    for part in (g("rev-parse", "HEAD"), g("status", "--porcelain"), g("diff", "HEAD")):
        h.update(part.encode(errors="replace") + b"\0")
    for name in sorted(g("ls-files", "-o", "--exclude-standard", "-z").split("\0")):
        f = Path(wt) / name
        try:
            data = os.readlink(f).encode() if f.is_symlink() else f.read_bytes() if f.is_file() else b""
        except OSError:
            data = b""
        h.update(name.encode(errors="replace") + b"\0" + data + b"\0")
    return h.hexdigest()


def _kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)  # the child runs in its own session; the terminal never signals it
    except OSError:
        pass
    p.wait()


def _on_term(*_):
    raise KeyboardInterrupt


def _spawn(spec, m, env, timeout_s):
    jd = _jobs() / m["job"]
    fds = [os.open(jd / n, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600) for n in ("stdout.txt", "stderr.txt")]
    try:
        old = signal.signal(signal.SIGTERM, _on_term)  # SIGTERM to lanes behaves like Ctrl-C
    except ValueError:  # not the main thread
        old = None
    env = {**env, "PWD": str(m["cwd"])}  # Popen(cwd=) leaves $PWD at the caller's dir; opencode trusts $PWD
    try:
        with open(spec["stdin"], "rb") as stdin:
            p = subprocess.Popen(spec["argv"], cwd=m["cwd"], env=env, stdin=stdin, stdout=fds[0], stderr=fds[1],
                                 start_new_session=True)
            if m.get("status") == "running":  # a dead wrapper's reaper needs this to find the agent
                m["pgid"] = p.pid
                _save_meta(m)
            try:
                return p.wait(timeout_s)
            except subprocess.TimeoutExpired:
                _kill(p)
                return 124
            except BaseException:  # Ctrl-C / SIGTERM: never leave a write-capable CLI running
                _kill(p)
                raise
    except OSError:
        return 127  # binary missing / cwd gone
    finally:
        if old is not None:
            signal.signal(signal.SIGTERM, old)
        for fd in fds:
            os.close(fd)


def _write_answer(adapter, mode, jd, stdout):
    """out.txt = the CLI's own {out} file when it wrote one, else the adapter's answer_field from
    stdout JSON, else raw stdout."""
    out = jd / "out.txt"
    tpl = adapter["run"].get(mode) or adapter["run"]["write"]
    if any("{out}" in t for t in tpl) and out.exists() and out.stat().st_size:
        return out.read_text()
    text, field = stdout, adapter.get("answer_field")
    if field:
        try:
            v = json.loads(stdout)[field]
            text = v if isinstance(v, str) else stdout
        except (ValueError, KeyError, TypeError):
            pass
    _put(out, text)
    return text


def _denied(stdout):
    """Names (never targets) of the tool actions a headless CLI refused, from its JSON `denied_actions`."""
    try:
        acts = json.loads(stdout)["denied_actions"]
        return sorted({a["action"] for a in acts if isinstance(a, dict) and isinstance(a.get("action"), str)})
    except (ValueError, KeyError, TypeError):
        return []


def _execute(m):
    jd, adapters, cfg = _jobs() / m["job"], load_adapters(), state.load_config()
    a, spec = adapters[m["cli"]], json.loads((_jobs() / m["job"] / "argv.json").read_text())
    before = _read_quota(m, adapters, cfg)
    rc = _spawn(spec, m, child_env(a, "read" if m["mode"] == "read" else "write"), m["timeout_s"])
    t1 = time.time()
    after = _read_quota(m, adapters, cfg) if before else None
    delta = _delta(before, after) if before and after and not _overlapped(m, t1) else None
    stdout, stderr = ((jd / n).read_text(errors="replace") for n in ("stdout.txt", "stderr.txt"))
    answer = _write_answer(a, m["mode"], jd, stdout)
    fail = classify_failure(a, rc, stdout, stderr)
    notes = []
    if _denied(stdout):
        notes.append("tools denied: " + ", ".join(_denied(stdout)))
    empty = bool(a.get("answer_field")) and not answer.strip()  # exit 0 without an answer is not a success
    if empty:
        notes.append("empty answer")
    if notes:
        m["note"] = "; ".join(notes)
    if fail == "limit":
        status = "limit"
        _mark_exhausted(m["pool"])
    elif m["worktree"]:  # a write job is judged by what changed in its worktree, not by the exit code
        try:
            changed = _snapshot(m["worktree"]) != m["snap"]
        except GitFailed as e:
            _finish(m, "failed", rc, delta, str(e))
            return
        status = ("done" if rc == 0 else "partial") if changed else "no_changes" if rc == 0 else "failed"
    elif empty and rc == 0:  # a write job with a worktree is judged by its diff instead (above)
        status, fail = "failed", "empty answer"
    else:
        status = "done" if rc == 0 else "failed"
    _finish(m, status, rc, delta, fail if status == "failed" else None)


def _plan_check(lane, pool, mode, meta, adapters, cfg, now, allow_spend):
    """Per-window est_cost of this job. Overage-capable pools are re-read and must fit (exit 4)."""
    tier = meta.get("tier") or "T1"
    kind = meta.get("kind") or ("read" if mode == "read" else "write")
    ledger = state.read_ledger()
    cost = lambda ws: {w.name: pick.estimate_cost(lane["pool"], tier, kind, w.name, ledger) for w in ws}
    if not pool.get("overage"):
        return cost([Window.from_dict(w) for w in pool.get("windows") or []])
    ws = discover.read_pool_windows(lane["pool"], adapters, cfg, now)
    # a model-scoped window (weekly:fable) only constrains that model
    ws = [w for w in ws or [] if ":" not in w.name or w.name.split(":", 1)[1] in lane["model"]]
    est, busy = cost(ws), _busy_cost(lane["pool"], None)
    if not allow_spend and (not ws or any(
            100 - effective_used(w, now) - busy.get(w.name, 0) < est[w.name] for w in ws)):
        _die(4, f"approval needed: this job would cross the plan limit of {lane['pool']} "
                f"(pass --allow-spend to accept overage)")
    return est


def exec_job(job_id):
    """Perform a job prepared by start_job (also the entry point of background runs)."""
    m = _load_meta(_jid(job_id))
    if m is None or m.get("status") != "running":
        return m
    m["pid"] = os.getpid()
    _save_meta(m)
    try:
        _execute(m)
    except BaseException:
        if m["status"] == "running":  # never leave a job "running"
            _finish(m, "failed", None, failure="error")
        raise
    return m


def _rollback(jd, cwd, wt, branch):
    """Undo a half-prepared job: its dir, and a worktree + branch this call created."""
    if wt is not None:
        discover.run_cmd(["git", "-C", str(cwd), "worktree", "remove", "--force", str(wt)], 60)
        shutil.rmtree(wt, ignore_errors=True)
        discover.run_cmd(["git", "-C", str(cwd), "worktree", "prune"], 60)
        discover.run_cmd(["git", "-C", str(cwd), "branch", "-D", branch], 60)
    shutil.rmtree(jd, ignore_errors=True)


def refuse_nested():
    if os.environ.get("LANES_DEPTH"):
        _die(3, "nested delegation refused: lanes run is not allowed inside a lanes worker")


def start_job(lane, mode, prompt_file, *, cwd, worktree, resume, bg, allow_spend, timeout_s, meta):
    """Validate, prepare jobs/<id>/ (and a worktree for write jobs), then run it (inline, or detached
    when bg). Refusals exit with a code: 3 nested, 4 spend approval, 2 unavailable / no git repo."""
    refuse_nested()
    adapters, cfg = load_adapters(), state.load_config()
    a = adapters.get(lane["cli"])
    if a is None:
        _die(2, f"unknown cli: {lane['cli']}")
    pool = ((state.read_inventory(None) or {}).get("pools") or {}).get(lane["pool"])
    if pool is None:
        _die(2, "run lanes discover first")
    if not _in_access(pool, lane):  # billing comes from the pool, so the lane must really belong to it
        _die(2, f"lane not in discovered access for {lane['pool']}")
    if mode != "read" and not (worktree or resume):
        _die(2, "write jobs need --worktree")
    if pool["billing"] in ("api", "unknown") and not allow_spend:
        _die(4, f"approval needed: {lane['pool']} is {pool['billing']}-billed")
    now = time.time()
    if pool["status"] in discover.DEAD or (
            pool["status"] == "exhausted" and (pool.get("exhausted_until") or _soonest_reset(pool, now)) > now):
        _die(2, f"{lane['pool']} unavailable: {pool['status']}")
    # an overage pool's fit check and the job's est_cost reservation (its meta) are one atomic step
    with state.reserve_lock() if pool.get("overage") else contextlib.nullcontext():
        est = _plan_check(lane, pool, mode, meta, adapters, cfg, now, allow_spend)
        if mode == "write_shell" and not (a.get("can_shell") or (lane["cli"] == "agy" and cfg.get("agy_allow_shell"))):
            _die(2, f"{lane['cli']} cannot run shell jobs")
        cwd = Path(cwd).resolve()
        use_wt = mode != "read" and bool(worktree or resume)
        if use_wt and not resume and discover.run_cmd(["git", "-C", str(cwd), "rev-parse", "--git-dir"], 30).returncode:
            _die(2, "write jobs need a git repo")

        jid = state.new_job_id()
        jd = state.job_dir(jid)
        os.chmod(jd, 0o700)
        wt = branch = snap = None
        made_wt = False
        try:
            if use_wt:
                if resume:
                    wt, branch = state.CACHE_DIR() / "worktrees" / _jid(resume), "lanes/" + resume
                    if not wt.is_dir():
                        _die(2, f"unknown worktree: {resume}")
                else:
                    wt, branch = state.CACHE_DIR() / "worktrees" / jid, "lanes/" + jid
                    r = discover.run_cmd(["git", "-C", str(cwd), "worktree", "add", "-b", branch, str(wt), "HEAD"], 60)
                    if r.returncode:
                        _die(2, "git worktree add failed (does the repo have a commit?)")
                    made_wt = True
                snap = _snapshot(wt)
            _put(jd / "prompt.txt", PREAMBLE + "\n\n" + Path(prompt_file).read_text())
            for n in ("stdout.txt", "stderr.txt", "out.txt"):
                _put(jd / n)
            argv, stdin = build_argv(a, lane, mode, jd / "prompt.txt", jd / "out.txt", timeout_s, wt or cwd)
            _put(jd / "argv.json", json.dumps({"argv": argv, "stdin": stdin}))
            _save_meta({"job": jid, "status": "running", "pool": lane["pool"], "cli": lane["cli"],
                        "provider": lane.get("provider"), "model": lane["model"], "effort": lane.get("effort") or "",
                        "mode": mode, "tier": meta.get("tier"), "kind": meta.get("kind"),
                        "task": state.redact(str(meta.get("task") or ""))[:120], "cwd": str(wt or cwd),
                        "worktree": str(wt) if wt else None, "branch": branch, "snap": snap, "resume_of": resume,
                        "started": time.time(), "timeout_s": timeout_s, "est_cost": est})
        except GitFailed as e:
            _rollback(jd, cwd, wt if made_wt else None, branch)
            _die(2, str(e))
        except BaseException:
            _rollback(jd, cwd, wt if made_wt else None, branch)
            raise
    if bg:
        code = "import sys; sys.path.insert(0, sys.argv[1]); from lanelib import runner; runner.exec_job(sys.argv[2])"
        p = subprocess.Popen([sys.executable, "-I", "-c", code, str(SCRIPTS), jid], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.returncode = 0  # detached on purpose: nobody waits for it, so no "still running" ResourceWarning
    else:
        exec_job(jid)
    return jid


# ---- wait, mark, ledger ------------------------------------------------------------------------

def _reap(m):
    """A background job whose process vanished without finishing is failed, not running forever."""
    if m["status"] == "running" and m.get("pid") and not _alive(m["pid"]):
        if m.get("pgid"):
            try:  # ponytail: a recycled pgid could hit a stranger; check the leader's start time if it ever bites
                os.killpg(m["pgid"], signal.SIGKILL)
            except OSError:
                pass
        _finish(m, "failed", None, failure="died")
    return m


def wait_jobs(ids, timeout_s):
    """Block (2 s poll) until none of ids is running or timeout_s passes; -> one meta dict per id."""
    ids = [_jid(i) for i in ids]
    end = time.monotonic() + timeout_s
    while True:
        ms = [_reap(_load_meta(i) or {"job": i, "status": "unknown"}) for i in ids]
        left = end - time.monotonic()
        if left <= 0 or all(m["status"] != "running" for m in ms):
            return ms
        time.sleep(min(POLL, left))


def mark(job_id, outcome):
    """Record how the result turned out (e.g. accepted / rejected) on the job's ledger row."""
    m = _load_meta(_jid(job_id))
    if m is None:
        _die(2, f"unknown job: {job_id}")
    if m["status"] == "running":
        _die(2, f"job still running: {job_id}")
    state.update_ledger(job_id, outcome=state.redact(str(outcome))[:60])


def ledger(since=None):
    return state.read_ledger(since)
