from collections import defaultdict
from datetime import datetime, timezone


def calls_over_time(calls):
    per_day = defaultdict(lambda: {"total": 0, "answered": 0, "missed": 0})
    for c in calls:
        ts = c.get("date_added")
        if ts is None:
            continue
        if isinstance(ts, (int, float)):
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            day = str(ts)[:10]
        bucket = per_day[day]
        bucket["total"] += 1
        if c["status"] in ("completed", "answered"):
            bucket["answered"] += 1
        elif c["status"] in ("no-answer", "missed", "busy", "voicemail"):
            bucket["missed"] += 1
    return [{"date": day, **stats} for day, stats in sorted(per_day.items())]


def calls_by_status(calls):
    counts = defaultdict(int)
    for c in calls:
        counts[c["status"] or "unknown"] += 1
    return [{"status": status, "count": count} for status, count in sorted(counts.items(), key=lambda x: -x[1])]


def calls_by_agent(agent_rows):
    return [{"agent_name": r["agent_name"], "total_calls": r["total_calls"]} for r in agent_rows]
