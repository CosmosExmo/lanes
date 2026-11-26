"""Ranking: which lane should take this job. Use quota before it expires, keep long jobs off
tail-end pools, never auto-spend, keep reviews off the author's pool. Stdlib only."""
import fnmatch
import re
import statistics
from dataclasses import asdict, dataclass

from .quota import Window, effective_used, pace  # noqa: F401  (Window re-exported for callers)

EFFORT = {"T0": "low", "T1": "medium", "T2": "high"}
DEFAULT_COST = {"5h": {"S": 3, "M": 8, "L": 20},
                "weekly": {"S": 0.5, "M": 1.5, "L": 4},
                "monthly": {"S": 0.2, "M": 0.6, "L": 1.5}}
APPROVAL_STATUS = ("api_billing", "logged_in_unverified")  # listed only when billing is api/unknown


@dataclass
class Request:
    tier: str
    kind: str  # read | write | review
    task: str
    needs_shell: bool = False
    author_pool: str = None
    from_pool: str = None
    n: int = 3


@dataclass
class Candidate:
    pool: str
    cli: str  # "native" = the caller's own pool
    provider: str
    model: str
    effort: str
    fits: bool
    urgency: float
    brake: bool
    needs_approval: bool
    est_cost: dict
    reason: str

    def to_dict(self):
        return asdict(self)


def size_class(tier, kind):
    if tier == "T0" or (kind == "review" and tier == "T1"):
        return "S"
    return "L" if kind == "write" else "M"


def estimate_cost(pool, tier, kind, window, ledger):
    rows = [r["delta"][window] for r in ledger
            if r.get("pool") == pool and r.get("tier") == tier and r.get("kind") == kind
            and isinstance(r.get("delta"), dict) and window in r["delta"]
            and isinstance(r["delta"][window], (int, float)) and not isinstance(r["delta"][window], bool)
            and r["delta"][window] >= 0]
    if len(rows) >= 3:
        return statistics.median(rows[-10:])
    return DEFAULT_COST.get(window, DEFAULT_COST["weekly"])[size_class(tier, kind)]


def _is_candidate(p):
    if p.status in ("usable", "quota_unknown"):
        return True
    return p.status in APPROVAL_STATUS and p.billing in ("api", "unknown")


def _lane(p, entries, adapters, cfg, req):
    """First caps entry (cheap->expensive) x access entry (official CLI first) that matches a
    discovered model and passes the shell check -> (cap, access, model)."""
    access = p.access or [{"cli": p.cli, "provider": None, "models": p.models}]
    for cap in entries:
        if p.id != cap["pool"] or req.tier not in cap["tiers"]:
            continue
        for acc in access:
            hits = [m for m in acc["models"] if fnmatch.fnmatch(m, cap["pattern"])]
            if not hits:
                continue
            ad = adapters.get(acc["cli"], {})
            if req.needs_shell and not (ad.get("can_shell") or (acc["cli"] == "agy" and cfg.get("agy_allow_shell"))):
                continue
            style, eff = ad.get("effort_style"), EFFORT[req.tier]
            base = lambda m: re.sub(r"-(low|medium|high|xhigh)$", "", m)  # slugs may already carry an effort
            model = next((f"{base(m)}-{eff}" for m in hits if f"{base(m)}-{eff}" in acc["models"]), hits[0]) \
                if style == "model_suffix" else hits[0]
            return cap, acc, model, ("" if style == "none" else eff)
    return None


def rank(pools, caps, adapters, cfg, req, ledger, now):
    size = size_class(req.tier, req.kind)
    task = req.task.lower()
    out = []
    for p in pools.values():
        if not _is_candidate(p) or (p.id == req.author_pool and req.kind == "review"):
            continue
        lane = _lane(p, caps["models"], adapters, cfg, req)
        if lane is None:
            continue
        cap, acc, model, effort = lane
        native = p.id == req.from_pool
        # a model-scoped window (weekly:fable) only constrains that model
        scoped = [w for w in p.windows if ":" not in w.name or w.name.split(":", 1)[1] in model]
        # a window whose reset already passed is stale (its replacement is unread), not "0% used": treat as unknown
        ws = [w for w in scoped if w.resets_at > now]
        stale = len(ws) < len(scoped)
        hrs = {w.name: max((w.resets_at - now) / 3600, 0) for w in ws}
        cost = {w.name: estimate_cost(p.id, req.tier, req.kind, w.name, ledger) for w in ws}
        rem = {w.name: 100 - effective_used(w, now)
               - (cfg["orchestrator_reserve_pct"] * hrs[w.name] / (w.length_min / 60) if native else 0)
               for w in ws}
        fits = all(rem[n] >= cost[n] for n in rem)
        urgency, brake, reason = 0.0, False, f"{p.id} quota unknown"
        if stale and not ws:
            reason += " (stale: cached window already reset)"
        if ws:
            urg = {w.name: rem[w.name] / max(hrs[w.name], 0.25) for w in ws}
            long = max(ws, key=lambda w: w.length_min)
            pc = pace(long, now)
            urgency = urg[long.name] if pc > 1 else max(urg.values())
            brake = pc > 1 and size in ("M", "L")
            reason = (f"{p.id} {long.name} {effective_used(long, now):.0f}% used, resets in "
                      f"{hrs[long.name] / 24:.1f}d, pace {pc:.1f}, est {cost[long.name]:g}%"
                      + (", stale window ignored" if stale else ""))
        needs_approval = p.billing in ("api", "unknown") or (p.overage and not fits)
        if needs_approval:
            reason += ", needs approval"
        if not fits:
            continue
        out.append((needs_approval, brake, -sum(s in task for s in cap.get("strengths", [])), -urgency,
                    Candidate(p.id, "native" if native else acc["cli"], None if native else acc.get("provider"),
                              model, effort, fits, urgency, brake, needs_approval, cost, reason)))
    out.sort(key=lambda t: t[:4])
    return [t[4] for t in out[:req.n]]
