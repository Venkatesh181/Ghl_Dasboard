"""Groups scanned call records by agent for the Agent Performance table.

Native calls carry a real GHL userId. JustCall calls only carry a free-text
"Assigned To" name (JustCall's own text format, not a GHL field), so those are
matched back to a GHL user by name. An unmatched name is kept visible as-is
(prefixed) rather than silently dropped or merged into a guessed agent.
"""
from calls_service import ANSWERED_STATUSES, MISSED_STATUSES


def build_name_lookup(users):
    lookup = {}
    for u in users:
        full_name = (u.get("name") or f"{u.get('firstName', '')} {u.get('lastName', '')}").strip()
        if full_name:
            lookup[full_name.lower()] = u
    return lookup


def match_agent_by_name(name, lookup):
    if not name:
        return None
    key = name.strip().lower()
    if key in lookup:
        return lookup[key]
    for k, u in lookup.items():
        if key in k or k in key:
            return u
    return None


def compute_agent_performance(calls, users, agent_id_filter=None):
    lookup = build_name_lookup(users)
    id_to_user = {u["id"]: u for u in users}
    rows = {}

    for c in calls:
        if c["source"] == "native":
            agent_id = c.get("agent_user_id")
            user = id_to_user.get(agent_id) if agent_id else None
            if user:
                agent_name = user.get("name") or f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
                key = (agent_id, "native")
            else:
                agent_name = f"Unknown GHL user ({agent_id})" if agent_id else "Unassigned"
                key = (agent_id or "unassigned", "native")
        else:
            matched = match_agent_by_name(c.get("agent_name"), lookup)
            if matched:
                agent_id = matched["id"]
                agent_name = matched.get("name") or f"{matched.get('firstName', '')} {matched.get('lastName', '')}".strip()
            else:
                agent_id = None
                agent_name = f"Unmatched name: {c.get('agent_name')}" if c.get("agent_name") else "Unknown"
            key = (agent_id or f"unmatched:{agent_name}", "justcall")

        if agent_id_filter and agent_id != agent_id_filter:
            continue

        row = rows.setdefault(key, {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "source": c["source"],
            "total_calls": 0,
            "answered_calls": 0,
            "missed_calls": 0,
            "inbound_calls": 0,
            "outbound_calls": 0,
            "total_duration_seconds": 0,
        })
        row["total_calls"] += 1
        if c["direction"] == "inbound":
            row["inbound_calls"] += 1
        elif c["direction"] == "outbound":
            row["outbound_calls"] += 1
        if c["status"] in ANSWERED_STATUSES:
            row["answered_calls"] += 1
        if c["status"] in MISSED_STATUSES:
            row["missed_calls"] += 1
        row["total_duration_seconds"] += c["duration_seconds"] or 0

    result = []
    for row in rows.values():
        row["avg_call_duration_seconds"] = (
            round(row["total_duration_seconds"] / row["answered_calls"], 1) if row["answered_calls"] else 0
        )
        result.append(row)

    result.sort(key=lambda r: r["total_calls"], reverse=True)
    return result
