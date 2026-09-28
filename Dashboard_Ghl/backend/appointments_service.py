"""Builds the Appointments table by fetching calendar events across every
calendar in the location and resolving contact/agent names.

GHL's calendar events endpoint requires a specific calendarId per call (there
is no "all calendars" list endpoint), so this loops every calendar the
location has and merges results.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from ghl_client import GHLAPIError

CONTACT_FETCH_WORKERS = 8


def list_appointments(client, start_dt, end_dt, users_by_id, agent_id=None, status=None):
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    calendars = client.list_calendars()
    all_events = []

    def fetch_events(cal):
        try:
            return client.get_calendar_events(cal["id"], start_ms, end_ms)
        except GHLAPIError:
            return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_events, cal) for cal in calendars]
        for future in as_completed(futures):
            all_events.extend(future.result())

    if agent_id:
        all_events = [e for e in all_events if e.get("assignedUserId") == agent_id]
    if status:
        all_events = [e for e in all_events if (e.get("appointmentStatus") or "").lower() == status.lower()]

    contact_ids = sorted({e.get("contactId") for e in all_events if e.get("contactId")})
    contact_names = {}

    def fetch_contact(cid):
        try:
            c = client.get_contact(cid)
            return cid, (c.get("contactName") or c.get("name") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip())
        except GHLAPIError:
            return cid, None

    with ThreadPoolExecutor(max_workers=CONTACT_FETCH_WORKERS) as pool:
        futures = [pool.submit(fetch_contact, cid) for cid in contact_ids]
        for future in as_completed(futures):
            cid, name = future.result()
            contact_names[cid] = name

    rows = []
    for e in all_events:
        agent = users_by_id.get(e.get("assignedUserId"))
        rows.append({
            "id": e.get("id"),
            "customer_name": contact_names.get(e.get("contactId")) or "Unknown",
            "agent_name": (agent.get("name") if agent else None) or "Unassigned",
            "start_time": e.get("startTime"),
            "end_time": e.get("endTime"),
            "status": e.get("appointmentStatus") or "unknown",
            "title": e.get("title"),
        })
    rows.sort(key=lambda r: r["start_time"] or "", reverse=True)
    return rows
