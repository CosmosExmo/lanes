#!/usr/bin/env python3
"""lanes: spread agent work across your subscriptions. Thin CLI over lanelib."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from lanelib import discover, pick, runner, selftest, state  # noqa: E402


def out(text):
    print(state.redact(str(text)))


def emit(args, data, lines):
    if args.json:
        out(json.dumps(data))
    else:
        for l in lines:
            out(l)


def load_inv():
    inv = state.read_inventory(discover.QUOTA_TTL)
    if inv is None:
        inv = discover.discover(runner.load_adapters(), state.load_config(), False, False, time.time())
    return inv


def self_pool():
    return discover.detect_self_pool(os.environ, runner.load_adapters())


def fmt_pool(p, now):
    ws = []
    for w in p["windows"]:
        left = max(w["resets_at"] - now, 0)
        ws.append(f"{w['name']}={w['used_pct']:.0f}% (resets {int(left // 86400)}d)" if left >= 86400
                  else f"{w['name']}={w['used_pct']:.0f}% (resets {int(left // 3600)}h)")
    via = ",".join(a["cli"] for a in p.get("access") or []) or p["cli"]
    return "  ".join([p["id"], p["status"], p["billing"], *ws, "via " + via])


def cmd_discover(args):
    inv = discover.discover(runner.load_adapters(), state.load_config(), args.refresh, args.probe, time.time())
    now = time.time()
    emit(args, inv, [fmt_pool(p, now) for p in inv["pools"].values()])


def stale_windows(inv, now):
    return any(p["status"] in ("usable", "exhausted", "quota_unknown") and any(w["resets_at"] <= now for w in p["windows"])
               for p in inv["pools"].values())


def cmd_pick(args):
    inv = load_inv()
    if stale_windows(inv, time.time()):  # a cached window already reset: read the new one before ranking
        inv = discover.discover(runner.load_adapters(), state.load_config(), True, False, time.time())
    pools = {k: discover.Pool.from_dict(v) for k, v in inv["pools"].items()}
    caps = json.loads((REPO / "capabilities.json").read_text())
    req = pick.Request(args.tier, args.kind, args.task or "", args.needs_shell, args.author_pool,
                       own_pool(args.from_pool), args.n)
    cands = pick.rank(pools, caps, runner.load_adapters(), state.load_config(), req,
                      state.read_ledger(), time.time())
    emit(args, [c.to_dict() for c in cands],
         [f"{c.pool}  {c.cli}  {(c.provider + '/') if c.provider else ''}{c.model}  {c.effort}  "
          f"fits={c.fits} urgency={c.urgency:.1f} brake={c.brake} approval={c.needs_approval}  {c.reason}"
          for c in cands])


def own_pool(from_pool, required=False):
    """--from <pool> | none, else the detected harness pool; unknown + required is an error (never skip silently)."""
    if from_pool is not None:
        return None if from_pool == "none" else from_pool
    own = self_pool()
    if own is None and required:
        runner._die(2, "cannot tell your own pool; pass --from <pool> or --from none")
    return own


def cmd_run(args):
    runner.refuse_nested()
    if args.pool == own_pool(args.from_pool, True) and not args.allow_self:
        runner._die(2, f"{args.pool} is your own pool; use a native subagent")
    lane = {"pool": args.pool, "cli": args.cli, "provider": args.provider, "model": args.model,
            "effort": args.effort}
    jid = runner.start_job(lane, args.mode, args.prompt_file, cwd=os.getcwd(), worktree=args.worktree,
                           resume=args.resume, bg=args.bg, allow_spend=args.allow_spend,
                           timeout_s=args.timeout, meta={"tier": args.tier, "kind": args.kind, "task": args.task})
    if args.bg:
        emit(args, {"job": jid, "status": "running"}, [jid])
        return
    m = runner.wait_jobs([jid], 5)[0]
    ans = state.job_dir(jid) / "out.txt"
    m["answer"] = ans.read_text() if ans.is_file() else ""
    emit(args, m, [f"{jid} {m['status']}"] + ([m["answer"]] if m["answer"] else []))
    code = verdict([m])
    if code:
        raise SystemExit(code)


def verdict(ms):
    """0 all done; 2 still running (timeout); 6 any failed/limit/partial/no_changes. One stderr line per bad job."""
    code = 0
    pools = (state.read_inventory(None) or {}).get("pools") or {}
    for m in ms:
        st = m.get("status")
        if st == "done":
            continue
        if st == "running":
            code = code or 2
            sys.stderr.write(state.redact(f"{m['job']} running (wait timed out)") + "\n")
            continue
        line = f"{m.get('job')} {st} {m.get('pool')} {m.get('cli')}"
        if st == "limit":
            until = (pools.get(m.get("pool")) or {}).get("exhausted_until")
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(until)) if until else "its next reset"
            line += f"; pool exhausted until {when}; pick the next lane"
        if m.get("note"):
            line += f"; {m['note']}"
        if st == "partial":
            line += f"; the run failed after editing, worktree kept at {m.get('worktree')}; review or resume it"
        sys.stderr.write(state.redact(line) + "\n")
        code = 6
    return code


def cmd_wait(args):
    ms = runner.wait_jobs(args.ids, args.timeout)
    emit(args, ms, [f"{m['job']} {m['status']}" for m in ms])
    code = verdict(ms)
    if code:
        raise SystemExit(code)


def cmd_mark(args):
    runner.mark(args.id, args.outcome)
    emit(args, {"job": args.id, "outcome": args.outcome}, [f"{args.id} {args.outcome}"])


def parse_since(s):
    if s is None:
        return None
    if s[-1:] in "dh" and s[:-1].isdigit():
        return time.time() - int(s[:-1]) * (86400 if s[-1] == "d" else 3600)
    try:
        return float(s)
    except ValueError:
        runner._die(2, f"bad --since: {s}")


def cmd_ledger(args):
    rows = runner.ledger(parse_since(args.since))
    by_pool, by_out = {}, {}
    for r in rows:
        by_pool[r.get("pool") or "?"] = by_pool.get(r.get("pool") or "?", 0) + 1
        by_out[r.get("outcome") or "unmarked"] = by_out.get(r.get("outcome") or "unmarked", 0) + 1
    emit(args, {"jobs": len(rows), "by_pool": by_pool, "by_outcome": by_out},
         [f"jobs {len(rows)}"] + [f"pool {k} {v}" for k, v in sorted(by_pool.items())]
         + [f"outcome {k} {v}" for k, v in sorted(by_out.items())])


def cmd_selftest(args):
    adapters = runner.load_adapters()
    rows = selftest.selftest(adapters, args.live)
    emit(args, rows, [f"{r['cli']}  {r['status']}  {r['version']}"
                      + (f"  (verified {r['verified_version']})" if r["version"] and r["version"] not in str(r["verified_version"]) else "")
                      + (f"  missing {','.join(r['flags_missing'])}" if r["flags_missing"] else "") for r in rows])
    if selftest.failed(rows, adapters):
        raise SystemExit(5)


def cmd_install(args):
    acts = selftest.install(REPO, args.bin_only, args.dry_run)
    emit(args, acts, acts)


def cmd_update(args):
    rc = selftest.update(REPO)
    if rc:
        raise SystemExit(rc)
    cmd_selftest(argparse.Namespace(live=False, json=args.json))


def cmd_exec(args):
    runner.exec_job(args.id)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="lanes", epilog="exit codes: 0 ok, 2 usage/environment error or wait timeout, 3 nested, "
        "4 approval needed, 5 selftest failure, 6 job failed/limit/partial/no_changes")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        s = sub.add_parser(name, **kw)
        s.add_argument("--json", action="store_true")
        s.set_defaults(fn=fn)
        return s

    s = add("discover", cmd_discover)
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--probe", action="store_true")
    s = add("pick", cmd_pick)
    s.add_argument("--tier", required=True, choices=["T0", "T1", "T2"])
    s.add_argument("--kind", required=True, choices=["read", "write", "review"])
    s.add_argument("--task")
    s.add_argument("--needs-shell", action="store_true")
    s.add_argument("--author-pool")
    s.add_argument("--from", dest="from_pool")
    s.add_argument("--n", type=int, default=3)
    s = add("run", cmd_run)
    s.add_argument("--pool", required=True)
    s.add_argument("--cli", required=True)
    s.add_argument("--provider")
    s.add_argument("--model", required=True)
    s.add_argument("--effort", required=True)
    s.add_argument("--mode", required=True, choices=["read", "write", "write_shell"])
    s.add_argument("--prompt-file", required=True)
    s.add_argument("--bg", action="store_true")
    s.add_argument("--worktree", action="store_true")
    s.add_argument("--resume")
    s.add_argument("--allow-spend", action="store_true")
    s.add_argument("--allow-self", action="store_true")
    s.add_argument("--from", dest="from_pool", metavar="POOL|none",
                   help="the pool your own harness bills against (or none); needed when it cannot be detected")
    s.add_argument("--timeout", type=int, default=1800)
    s.add_argument("--tier")
    s.add_argument("--kind")
    s.add_argument("--task")
    s = add("wait", cmd_wait)
    s.add_argument("ids", nargs="+")
    s.add_argument("--timeout", type=int, default=1800)
    s = add("mark", cmd_mark)
    s.add_argument("id")
    s.add_argument("outcome", choices=["used", "reworked", "wasted"])
    s = add("ledger", cmd_ledger)
    s.add_argument("--since")
    s = add("selftest", cmd_selftest)
    s.add_argument("--live", action="store_true")
    s = add("install", cmd_install)
    s.add_argument("--bin-only", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    add("update", cmd_update)
    s = sub.add_parser("_exec")
    s.add_argument("id")
    s.set_defaults(fn=cmd_exec, json=False)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
