"""Pure quota parsers and pace math: CLI usage output -> normalized Windows."""
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass
class Window:
    name: str
    used_pct: float
    resets_at: float  # unix seconds
    length_min: int

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], float(d["used_pct"]), float(d["resets_at"]), int(d["length_min"]))


def window_name(length_min):
    if length_min == 300:
        return "5h"
    if length_min == 10080:
        return "weekly"
    if 40000 <= length_min <= 46000:
        return "monthly"
    return f"{length_min}m"


_LINE = re.compile(
    r"^Current (session|week)(?: \(([^)]*)\))?: (\d+(?:\.\d+)?)% used\s*·\s*resets (.+?) \(([\w/+-]+)\)\s*$",
    re.M,
)
_RESET = re.compile(r"^(?:([A-Za-z]{3}) (\d{1,2}) at )?(\d{1,2})(?::(\d{2}))?\s*([ap]m)$", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _claude_reset(text, tz, now):
    m = _RESET.match(text.strip())
    if not m:
        return None
    mon, day, hour, minute, ampm = m.groups()
    hour = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
    minute = int(minute or 0)
    now = now.astimezone(tz)
    if mon is None:  # time only: today, else tomorrow
        t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return t if t >= now else t + timedelta(days=1)
    month = _MONTHS.get(mon.lower())
    if month is None:
        return None
    for year in (now.year, now.year + 1):  # next occurrence >= now
        try:
            t = datetime(year, month, int(day), hour, minute, tzinfo=tz)
        except ValueError:
            return None
        if t >= now:
            return t
    return None


def parse_claude_usage(text, now):
    out = []
    for kind, label, pct, reset, zone in _LINE.findall(text):
        try:
            tz = ZoneInfo(zone)
        except Exception:
            continue
        t = _claude_reset(reset, tz, now)
        if t is None:
            continue
        if kind == "session":
            name, length = "5h", 300
        else:
            length = 10080
            name = "weekly" if (label or "all models").lower() == "all models" else f"weekly:{label.lower()}"
        out.append(Window(name, float(pct), t.timestamp(), length))
    return out


def parse_codex_ratelimits(result):
    rl = result.get("rateLimits") or {}
    ws = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if w:
            n = int(w["windowDurationMins"])
            ws.append(Window(window_name(n), float(w["usedPercent"]), float(w["resetsAt"]), n))
    c = rl.get("credits") or {}
    return ws, bool(c.get("hasCredits") and not c.get("unlimited"))


_AGY_POOLS = {"Gemini Models": "google:gemini", "Claude and GPT models": "google:claude-gpt"}
_AGY_MIN = {"5h": 300, "weekly": 10080}


def parse_agy_usage(data):
    pools = {}
    for g in data["command"]["data"]["groups"]:
        name = g["name"]
        key = _AGY_POOLS.get(name) or "google:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        for b in g.get("buckets", []):
            n = _AGY_MIN.get(b.get("window"))
            if n is None:
                continue
            reset = datetime.fromisoformat(b["reset_time"].replace("Z", "+00:00")).timestamp()
            pools.setdefault(key, []).append(
                Window(window_name(n), 100 * (1 - b["remaining_fraction"]), reset, n))
    return pools


def parse_cursor_usage(cur):
    start, end = int(cur["billingCycleStart"]) / 1000, int(cur["billingCycleEnd"]) / 1000
    n = round((end - start) / 60)
    return [Window(window_name(n), float(cur["planUsage"]["totalPercentUsed"]), end, n)]


def effective_used(w, now):
    return 0 if w.resets_at <= now else w.used_pct


def elapsed_pct(w, now):
    total = w.length_min * 60
    return max(0.0, min(100.0, 100 * (1 - (w.resets_at - now) / total)))


def pace(w, now):
    e = elapsed_pct(w, now)
    return 0.0 if e < 1 else effective_used(w, now) / e
