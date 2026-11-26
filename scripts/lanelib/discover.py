"""Discovery: ask each installed agent CLI what it can do, normalize into Pools. Stdlib only.

Every external command goes through run_cmd. Raw command output is never stored: parsers pick
only the fields they need, and notes are fixed strings. Any parser/reader failure means windows
`[]` and status quota_unknown, never 0% used.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from . import state
from .quota import (Window, effective_used, parse_agy_usage, parse_claude_usage,
                    parse_codex_ratelimits, parse_cursor_usage)

QUOTA_TTL = 900
MODELS_TTL = 86400
PROBE_TTL = 86400
PROBE_PROMPT = "Reply with exactly: OK"
CLAUDE_MODELS = ["haiku", "sonnet", "opus", "fable"]
DEAD = ("not_installed", "adapter_broken", "not_logged_in")
QWEN_CODING_HOSTS = ("coding.dashscope.aliyuncs.com", "coding-intl.dashscope.aliyuncs.com")
AGY_POOLS = ("google:gemini", "google:claude-gpt")

try:
    VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
except OSError:
    VERSION = "0"


@dataclass
class Pool:
    id: str
    cli: str
    status: str
    billing: str
    overage: bool
    windows: list
    models: list
    note: str
    checked_at: float
    access: list = field(default_factory=list)  # [{cli, provider|None, models}], official CLI first
    exhausted_until: float = 0.0  # set by the runner on a limit error; 0 = not marked

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["windows"] = [Window.from_dict(w) for w in d.get("windows", [])]
        return cls(**d)


# ---- plumbing -------------------------------------------------------------------------------

def run_cmd(argv, timeout, env=None, stdin_path=None):
    """Run argv (text mode). Never raises on failure: timeout -> rc 124, missing binary -> rc 127.
    `env` is the complete child environment (None inherits); stdin is /dev/null unless given."""
    stdin = open(stdin_path) if stdin_path else subprocess.DEVNULL
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=timeout, env=env, stdin=stdin)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return subprocess.CompletedProcess(argv, 124, out, "timeout")
    except OSError as e:
        return subprocess.CompletedProcess(argv, 127, "", type(e).__name__)
    finally:
        if stdin_path:
            stdin.close()


def fill_template(tpl, **kw):
    """Substitute {name} placeholders in an argv template, single pass (values are never rescanned)."""
    sub = lambda m: str(kw.get(m.group(1), m.group(0)))
    return [re.sub(r"\{(\w+)\}", sub, t) for t in tpl]


def child_env(a, mode=None):
    """The ONLY way to build a child CLI's environment (discovery, probes and Task 5's runner).

    Contract: os.environ minus the adapter's `env_strip` keys, plus adapter `env`, plus
    `env_mode[mode]` (mode is "read" or "write"; per-mode settings such as opencode's deny
    config live only there, so always pass the mode). Billing must be judged against this
    environment, never os.environ, because a stripped key never reaches the child."""
    env = {k: v for k, v in os.environ.items() if k not in a["env_strip"]}
    env.update(a.get("env", {}))
    env.update(a.get("env_mode", {}).get(mode, {}))
    return env


def _ts(now):
    return now.timestamp() if isinstance(now, datetime) else float(now)


def _dt(now):
    return now if isinstance(now, datetime) else datetime.fromtimestamp(now).astimezone()


def _home():
    return Path(os.path.expanduser("~"))


def _read_json(path):
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _json_cmd(a, args, timeout=30):
    """Run `<bin> args`, parse stdout as JSON (any exit code); {} when it is not JSON."""
    r = run_cmd([a["bin"]] + args, timeout, child_env(a))
    try:
        d = json.loads(r.stdout)
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _new(pid, cli, now, billing="subscription"):
    return Pool(pid, cli, "not_installed", billing, False, [], [], "", _ts(now))


def _gate(p, a):
    """True (and p marked) when the CLI is missing or its --help lacks a flag the adapter needs."""
    if not shutil.which(a["bin"]):
        p.status = "not_installed"
        return True
    r = run_cmd([a["bin"]] + a["help_cmd"][1:], 20, child_env(a))
    text = r.stdout + r.stderr
    missing = [f for f in a["required_flags"] if f not in text]
    if missing:
        p.status, p.note = "adapter_broken", "missing flags: " + ", ".join(missing)
        return True
    return False


def _fin(p, provider=None):
    if p.status not in DEAD:
        p.access = [{"cli": p.cli, "provider": provider, "models": list(p.models)}]
    return p


def _quota_status(ws, now):
    if not ws:
        return "quota_unknown"
    return "exhausted" if any(effective_used(w, _ts(now)) >= 100 for w in ws if ":" not in w.name) else "usable"


def _apply_quota(p, ws, now):
    p.windows, p.status = ws, _quota_status(ws, now)
    if not ws:
        p.note = "quota parse failed"


def _prev(prev, pid, flag):
    """Previous inventory record for pid when its `flag` freshness holds."""
    return prev["pools"].get(pid) if prev and prev.get(flag) else None


def _cached_quota(prev, pid):
    """(windows, overage) from a still-fresh previous record, else None."""
    q = _prev(prev, pid, "quota_ok")
    if q and q["windows"]:
        return [Window.from_dict(w) for w in q["windows"]], q["overage"]
    return None


def _models(prev, pid, lister):
    m = _prev(prev, pid, "models_ok")
    return list(m["models"]) if m and m["models"] else lister()


# ---- readers (raise on any failure) -------------------------------------------------------

class _CmdFailed(Exception):
    def __init__(self, rc):
        super().__init__(rc)
        self.rc = rc


def _read_claude(a, now):
    r = run_cmd([a["bin"], "-p", "/usage", "--output-format", "json"], 60, child_env(a))
    if r.returncode:
        raise _CmdFailed(r.returncode)
    return parse_claude_usage(json.loads(r.stdout)["result"], _dt(now))


def _read_codex(a, now):
    """-> (windows, overage) via `codex app-server` JSON-RPC over stdio, 10 s budget."""
    msgs = [{"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "lanes", "version": VERSION}}},
            {"method": "initialized"}, {"id": 2, "method": "account/rateLimits/read"}]
    p = subprocess.Popen([a["bin"], "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, env=child_env(a))
    q = queue.Queue()

    def pump():
        for line in p.stdout:
            q.put(line)
        q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    try:
        p.stdin.write("".join(json.dumps(m) + "\n" for m in msgs))
        p.stdin.flush()
        end = time.monotonic() + 10
        while True:
            line = q.get(timeout=max(0.01, end - time.monotonic()))
            if line is None:
                raise _CmdFailed(p.poll())
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("id") == 2:
                return parse_codex_ratelimits(m["result"])  # KeyError on an error reply
    finally:
        p.kill()
        p.wait()
        for f in (p.stdin, p.stdout):
            try:
                f.close()
            except OSError:
                pass


def _read_agy(a):
    r = run_cmd([a["bin"], "-p", "/usage", "--output-format", "json"], 60, child_env(a))
    if r.returncode:
        raise _CmdFailed(r.returncode)
    return parse_agy_usage(json.loads(r.stdout))


def _cursor_token():
    r = run_cmd(["security", "find-generic-password", "-s", "cursor-access-token", "-w"], 10)
    if r.returncode or not r.stdout.strip():
        raise _CmdFailed(r.returncode)
    return r.stdout.strip()


def _cursor_post(method, token):
    req = urllib.request.Request(
        "https://api2.cursor.sh/aiserver.v1.DashboardService/" + method, data=b"{}",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "Connect-Protocol-Version": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _read_cursor(now):
    """-> (windows, overage); only called when config cursor_read_quota is true."""
    tok = _cursor_token()
    ws = parse_cursor_usage(_cursor_post("GetCurrentPeriodUsage", tok))
    return ws, _cursor_post("GetHardLimit", tok).get("noUsageBasedAllowed") is False


# ---- per-CLI checks -----------------------------------------------------------------------

def check_claude(adapters, now, prev=None):
    a = adapters["claude"]
    p = _new("anthropic", "claude", now)
    if _gate(p, a):
        return [p]
    st = _json_cmd(a, ["auth", "status"], 20)
    if not st.get("loggedIn"):
        p.status = "not_logged_in"
        return [p]
    if st.get("authMethod") != "claude.ai":
        p.status, p.billing = "api_billing", "api"
        return [p]
    p.models = list(CLAUDE_MODELS)
    try:
        ws = (_cached_quota(prev, p.id) or (_read_claude(a, now), False))[0]
    except Exception:
        ws = []
    _apply_quota(p, ws, now)
    return [_fin(p)]


def _codex_models(a):
    r = run_cmd([a["bin"], "debug", "models"], 30, child_env(a))
    try:
        return [m["slug"] for m in json.loads(r.stdout)["models"] if m.get("visibility") == "list"]
    except (ValueError, KeyError, TypeError):
        return []


def check_codex(adapters, now, prev=None):
    a = adapters["codex"]
    p = _new("chatgpt", "codex", now)
    if _gate(p, a):
        return [p]
    mode = _read_json(_home() / ".codex" / "auth.json").get("auth_mode")
    if not mode:
        p.status = "not_logged_in"
        return [p]
    if mode != "chatgpt":
        p.status, p.billing = "api_billing", "api"
        return [p]
    p.models = _models(prev, p.id, lambda: _codex_models(a))
    try:
        ws, p.overage = _cached_quota(prev, p.id) or _read_codex(a, now)
    except Exception:
        ws = []
    _apply_quota(p, ws, now)
    return [_fin(p)]


def _agy_models(a):
    r = run_cmd([a["bin"], "models"], 30, child_env(a))
    out = {pid: [] for pid in AGY_POOLS}
    for line in r.stdout.splitlines():
        if "\t" in line:
            mid = line.split("\t")[0].strip()
            out[AGY_POOLS[1] if mid.startswith(("claude-", "gpt-")) else AGY_POOLS[0]].append(mid)
    return out


def _agy_overage(a, settings):
    if not settings.get("useG1Credits"):
        return False
    d = _json_cmd(a, ["-p", "/credits", "--output-format", "json"], 30)
    try:
        return float(d["command"]["data"]["remaining_credits"]) > 0
    except (KeyError, TypeError, ValueError):
        return False


def check_agy(adapters, now, prev=None):
    a = adapters["agy"]
    ps = {pid: _new(pid, "agy", now) for pid in AGY_POOLS}
    if _gate(ps[AGY_POOLS[0]], a):
        for p in ps.values():
            p.status, p.note = ps[AGY_POOLS[0]].status, ps[AGY_POOLS[0]].note
        return list(ps.values())
    settings = _read_json(_home() / ".gemini" / "antigravity-cli" / "settings.json")
    api = settings.get("modelProvider") == "gemini"  # GEMINI_API_KEY alone has no effect (and is stripped)
    ws, overage = {}, False
    if not api:
        cached = [_prev(prev, pid, "quota_ok") for pid in AGY_POOLS]
        if all(c and c["windows"] for c in cached):
            ws = {pid: [Window.from_dict(w) for w in c["windows"]] for pid, c in zip(AGY_POOLS, cached)}
            overage = cached[0]["overage"]
        else:
            try:
                ws = _read_agy(a)
                overage = _agy_overage(a, settings)
            except _CmdFailed as e:
                api = e.rc == 2
            except Exception:
                pass
    if api:
        p = ps[AGY_POOLS[0]]
        p.status, p.billing = "api_billing", "api"
        return [p]
    cached = [_prev(prev, pid, "models_ok") for pid in AGY_POOLS]
    if all(c and c["models"] for c in cached):
        models = {pid: c["models"] for pid, c in zip(AGY_POOLS, cached)}
    else:
        models = _agy_models(a)
    for pid, p in ps.items():
        p.models, p.overage = models.get(pid, []), overage
        _apply_quota(p, ws.get(pid, []), now)
        _fin(p)
    return list(ps.values())


def _cursor_models(a):
    r = run_cmd([a["bin"], "--list-models"], 30, child_env(a))
    return [m.group(1) for m in (re.match(r"^([\w.\-]+) - ", l.strip()) for l in r.stdout.splitlines()) if m]


def check_cursor(adapters, cfg, now, prev=None):
    a = adapters["cursor"]
    p = _new("cursor", "cursor", now)
    if _gate(p, a):
        return [p]
    if not _json_cmd(a, ["status", "--format", "json"], 20).get("isAuthenticated"):
        p.status = "not_logged_in"
        return [p]
    tier = str(_json_cmd(a, ["about", "--format", "json"], 20).get("subscriptionTier", ""))
    p.note = ("tier " + tier) if re.fullmatch(r"[\w .+-]{1,30}", tier) else ""
    p.models = _models(prev, p.id, lambda: _cursor_models(a))
    p.status = "logged_in_unverified"
    if cfg.get("cursor_read_quota"):
        try:
            ws, p.overage = _cached_quota(prev, p.id) or _read_cursor(now)
        except Exception:
            ws = []
        _apply_quota(p, ws, now)
    return [_fin(p)]


def check_kimi(adapters, now, prev=None):
    a = adapters["kimi"]
    p = _new("kimi", "kimi", now)
    if _gate(p, a):
        return [p]
    r = run_cmd([a["bin"], "provider", "list"], 20, child_env(a))
    if r.returncode or not re.search(r"\btype=", r.stdout):
        p.status = "not_logged_in"
        return [p]
    m = re.search(r"Default model:\s*(?:[\w.-]+/)?([\w.-]+)", r.stdout)
    p.models = [m.group(1)] if m else []
    p.status = "logged_in_unverified"  # no quota reader; usable only after a probe
    return [_fin(p)]


# ---- routers (pi, opencode, qwen) ----------------------------------------------------------

def _routed(a, name, provider, models, now, pools, conf=None):
    """Pool for one routable provider; provider ids missing from the adapter map get billing unknown."""
    conf = conf or a["providers"].get(provider) or {"pool": "unknown:" + provider, "billing": "unknown", "route": True}
    if not conf["route"]:
        return None
    p = _new(conf["pool"], name, now, conf["billing"])
    p.status = "api_billing" if conf["billing"] == "api" else "logged_in_unverified"
    p.models = models
    off = (pools or {}).get(p.id)
    if off and off.windows:
        p.windows = list(off.windows)
    return _fin(p, provider)


def _pi_providers(a):
    """provider -> models, for providers pi holds credentials for (key names only; never values)."""
    cand = set(_read_json(_home() / ".pi" / "agent" / "auth.json"))
    models = {}
    for line in run_cmd([a["bin"], "--list-models"], 30, child_env(a)).stdout.splitlines():
        cols = line.split()
        if len(cols) >= 2 and cols[0] != "provider":
            models.setdefault(cols[0], []).append(cols[1])
    cand |= set(models)
    out = {}
    for prov in sorted(cand):
        st = _json_cmd(a, ["auth", "check", "--provider", prov, "--json", "--no-refresh"], 20)
        if st.get("status") == "ready":
            out[prov] = models.get(prov, [])
    return out


def _opencode_providers(a):
    """provider -> (models, has_oauth) for active providers that have a stored/env connection."""
    prov = _json_cmd(a, ["api", "provider.list"]).get("data") or []
    conns = {i.get("id"): i.get("connections") or []
             for i in _json_cmd(a, ["api", "integration.list"]).get("data") or []}
    models = {}
    for line in run_cmd([a["bin"], "models"], 30, child_env(a)).stdout.splitlines():
        if "/" in line:
            pv, m = line.strip().split("/", 1)
            models.setdefault(pv, []).append(m)
    out = {}
    for pr in prov:
        cs = conns.get(pr.get("integrationID") or pr.get("id"))
        if cs:
            out[pr["id"]] = (models.get(pr["id"], []), any(c.get("method") == "oauth" for c in cs))
    return out


def _qwen_base_url(settings, a):
    env = child_env(a).get("OPENAI_BASE_URL")
    if env:
        return env
    entries = (settings.get("modelProviders") or {}).get("openai") or []
    want = (settings.get("model") or {}).get("name")
    for e in entries:
        if isinstance(e, dict) and (e.get("id") == want or want is None):
            return e.get("baseUrl") or e.get("baseURL")
    if entries and isinstance(entries[0], dict):
        return entries[0].get("baseUrl") or entries[0].get("baseURL")
    return None


def _check_qwen(adapters, now):
    a = adapters["qwen"]
    pool = a["providers"]["qwen-subscription"]["pool"]
    p = _new(pool, "qwen", now)
    if _gate(p, a):
        return [p]
    s = _read_json(_home() / ".qwen" / "settings.json")
    sel = ((s.get("security") or {}).get("auth") or {}).get("selectedType")
    signed = "tokens" in _read_json(_home() / ".qwen" / "subscription_credentials.json")
    if sel == "openai":
        host = urlparse(_qwen_base_url(s, a) or "").hostname
        kind = "subscription" if host in QWEN_CODING_HOSTS else "api"
    elif sel in (None, "qwen-subscription"):
        kind = "subscription" if signed else None
    else:
        kind = "api"
    if kind is None:
        p.status = "not_logged_in"
        return [p]
    if kind == "api":
        p.id, p.billing, p.status = "api:qwen", "api", "api_billing"
        return [p]
    name = (s.get("model") or {}).get("name")
    p.models = [name] if isinstance(name, str) else []
    p.status = "logged_in_unverified"
    return [_fin(p, "qwen-subscription")]


def check_router(name, adapters, now, pools=None):
    """One Pool per routable provider with credentials. `pools` (id -> Pool) supplies the official
    CLI's quota windows when it already read them."""
    if name == "qwen":
        return _check_qwen(adapters, now)
    a = adapters[name]
    p = _new("router:" + name, name, now)
    if _gate(p, a):
        return [p] if p.status == "adapter_broken" else []
    if name == "pi":
        found = {prov: (ms, None) for prov, ms in _pi_providers(a).items()}
    else:
        found = {}
        for prov, (models, oauth) in _opencode_providers(a).items():
            # openai with a key/env connection only is pay-per-use, not the ChatGPT plan
            api = prov == "openai" and not oauth
            found[prov] = (models, {"pool": "api:openai", "billing": "api", "route": True} if api else None)
    return [x for x in (_routed(a, name, prov, ms, now, pools, conf) for prov, (ms, conf) in found.items()) if x]


# ---- probe, discover, self-detection --------------------------------------------------------

def _probe(adapters, pool, cli=None):
    """-> (ok, limit_error): one read-only prompt through the pool's first access CLI (or the one named `cli`)."""
    if pool.billing != "subscription" or pool.status in DEAD or not pool.access:
        return False, False  # money rule: api/unknown billing is never probed
    acc = next((x for x in pool.access if cli in (None, x["cli"])), None)
    if acc is None:
        return False, False
    a = adapters[acc["cli"]]
    pm = a["probe_model"]
    if isinstance(pm, dict):  # per-provider pick for CLIs that route several providers
        pm = pm.get(acc["provider"])
    # a stale or restricted pick must not decide the probe: fall back to the first listed model
    model = pm if pm and (not acc["models"] or pm in acc["models"]) else (acc["models"] or [""])[0]
    if not model:
        return False, False
    with tempfile.TemporaryDirectory() as d:
        pf, out = os.path.join(d, "prompt.txt"), os.path.join(d, "out.txt")
        Path(pf).write_text(PROBE_PROMPT)
        argv = fill_template(a["run"]["read"], model=model, effort="low", prompt=PROBE_PROMPT,
                             prompt_file=pf, out=out, provider=acc["provider"] or "", timeout="60")
        argv += a.get("nesting_guard", [])
        r = run_cmd(argv, 120, child_env(a, "read"), pf if a["stdin"] == "prompt" else None)
        try:
            text = r.stdout + r.stderr + Path(out).read_text()
        except OSError:
            text = r.stdout + r.stderr
    ok = r.returncode == 0 and re.search(r"\bOK\b", text) is not None
    return ok, (not ok) and is_limit_error(a, text)


def is_limit_error(a, text):
    """True when CLI output contains one of the adapter's limit/plan error strings (case-insensitive substring)."""
    low = text.lower()
    return any(x.lower() in low for x in a["limit_error_patterns"])


def probe(adapters, pool, cli=None):
    return _probe(adapters, pool, cli)[0]


# Env vars each harness sets in its tool shell (seen live 2026-11-26).
SELF_MARKERS = (("CODEX_THREAD_ID", "chatgpt"), ("ANTIGRAVITY_AGENT", "google:gemini"), ("CURSOR_AGENT", "cursor"))


def detect_self_pool(env, adapters=None):
    """Pool the calling harness bills against, or None.

    Order matters because a child harness inherits its parent's markers: pi first (it is the one that
    runs under Codex/Claude shells and sets its own), then Codex, agy and cursor, and CLAUDECODE last
    (any CLI started from Claude Code's shell inherits CLAUDECODE=1). pi names its pool in PI_PROVIDER;
    only a routed subscription provider maps (never an api-billed or unrouted one such as anthropic),
    anything else is None, and pi never falls through to a parent's marker. opencode sets
    OPENCODE_SESSION_ID but no provider variable, so its pool is unknowable."""
    if env.get("PI_CODING_AGENT"):
        prov = ((adapters or {}).get("pi") or {}).get("providers", {}).get(env.get("PI_PROVIDER", ""), {})
        return prov.get("pool") if prov.get("route") and prov.get("billing") == "subscription" else None
    for var, pool in SELF_MARKERS:
        if env.get(var):
            return pool
    return "anthropic" if env.get("CLAUDECODE") == "1" else None


def read_pool_windows(pool_id, adapters, cfg, now):
    """Fresh quota windows for one pool via its official CLI's reader; None when there is none."""
    try:
        if pool_id == "anthropic":
            ws = _read_claude(adapters["claude"], now)
        elif pool_id == "chatgpt":
            ws = _read_codex(adapters["codex"], now)[0]
        elif pool_id in AGY_POOLS:
            ws = _read_agy(adapters["agy"]).get(pool_id)
        elif pool_id == "cursor" and cfg.get("cursor_read_quota"):
            ws = _read_cursor(now)[0]
        else:
            return None
        return ws or None
    except Exception:
        return None


def _merge(pools, p):
    cur = pools.get(p.id)
    # a subscription route replaces a dead or pay-per-use official record
    if cur is None or cur.status in DEAD or (cur.billing == "api" and p.billing == "subscription"):
        pools[p.id] = p
    else:
        cur.access += p.access
        cur.models = cur.models or p.models


def discover(adapters, cfg, refresh, do_probe, now):
    now = _ts(now)
    old = state.read_inventory(None) or {}
    prev = None if refresh or not old else {
        "pools": old.get("pools", {}),
        "quota_ok": now - old.get("quota_at", 0) < QUOTA_TTL,
        "models_ok": now - old.get("models_at", 0) < MODELS_TTL}
    pools = {}

    def run(fallback_id, cli, fn):
        try:
            return fn()
        except Exception:
            return [Pool(fallback_id, cli, "adapter_broken", "unknown", False, [], [], "check failed", now)]

    for ps in (run("anthropic", "claude", lambda: check_claude(adapters, now, prev)),
               run("chatgpt", "codex", lambda: check_codex(adapters, now, prev)),
               run("google:gemini", "agy", lambda: check_agy(adapters, now, prev)),
               run("cursor", "cursor", lambda: check_cursor(adapters, cfg, now, prev)),
               run("kimi", "kimi", lambda: check_kimi(adapters, now, prev))):
        for p in ps:
            pools[p.id] = p
    for name in ("pi", "opencode", "qwen"):
        for p in run("router:" + name, name, lambda: check_router(name, adapters, now, pools)):
            _merge(pools, p)

    for pid, p in pools.items():  # probe results (usable/no_plan without quota) live for 24 h
        o = old.get("pools", {}).get(pid)
        if p.status != "logged_in_unverified" or p.billing != "subscription":
            continue  # api/unknown billing is never probed (a probe spends money)
        if o and o["status"] in ("usable", "no_plan") and not o["windows"] \
                and now - o["checked_at"] < PROBE_TTL and not (refresh and do_probe):
            p.status, p.note, p.checked_at = o["status"], o["note"], o["checked_at"]
        elif do_probe:
            ok, limit = _probe(adapters, p)
            p.status = "usable" if ok else "no_plan" if limit else p.status
            p.note = "" if ok else "probe: plan or limit error" if limit else "probe failed"
    for pid, p in pools.items():  # a limit error outranks fresh quota until its reset time passes
        until = (old.get("pools", {}).get(pid) or {}).get("exhausted_until") or 0.0
        if until > now:
            p.status, p.exhausted_until = "exhausted", until
    inv = {"version": 1, "checked_at": now,
           "quota_at": old.get("quota_at", now) if prev and prev["quota_ok"] else now,
           "models_at": old.get("models_at", now) if prev and prev["models_ok"] else now,
           "pools": {pid: p.to_dict() for pid, p in pools.items()}}
    state.write_inventory(inv)
    return inv
