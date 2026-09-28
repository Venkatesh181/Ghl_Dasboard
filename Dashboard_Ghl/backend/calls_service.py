"""
Scans GHL conversations for call activity within a date range and classifies
each call message as either a native structured GHL call (messageType TYPE_CALL)
or a JustCall-integration call logged as text (see call_parser.py).

Conversations are paginated newest-first by last_message_date and the scan stops
once a conversation's last activity falls before the requested start date - since
a conversation's last_message_date is always >= the date of any message inside it,
this correctly bounds the scan to every conversation that could contain a call in
range, without requiring a native date-range filter (the GHL conversations search
API does not offer one).
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from call_parser import parse_justcall_body
from ghl_client import GHLAPIError

MESSAGE_FETCH_WORKERS = 8
CACHE_TTL_SECONDS = 20

_cache_lock = threading.Lock()
_cache = {}  # (start_ms, end_ms) -> (expires_at, calls, truncated)

SCAN_SAFETY_CAP = 3000  # max conversations to walk per request, to bound worst-case latency


def _iso_to_ms(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _classify_message(message, conversation_id, contact_info):
    message_type = message.get("messageType")
    contact_info = contact_info or {}

    if message_type == "TYPE_CALL":
        meta_call = (message.get("meta") or {}).get("call") or {}
        status = meta_call.get("status") or message.get("status")
        # GHL's native call recording lives behind a separate endpoint (not a field
        # on the message), so there's no way to know in advance whether one exists.
        # "completed" (a talked-to-someone call) and "voicemail" (the caller left an
        # actual audio message) are the only statuses that can have real audio - a
        # no-answer/busy call was never connected, so there's nothing to record.
        recording_kind = "native" if status in ("completed", "voicemail") else None
        return {
            "source": "native",
            "is_voice_ai": message.get("subType") == "VOICE_AI",
            "conversation_id": conversation_id,
            "message_id": message.get("id"),
            "direction": message.get("direction"),
            "status": status,
            "duration_seconds": meta_call.get("duration"),
            "agent_user_id": message.get("userId") or None,
            "agent_name": None,
            "contact_id": message.get("contactId"),
            "contact_name": contact_info.get("name"),
            "contact_phone": contact_info.get("phone"),
            "from_number": message.get("from"),
            "to_number": message.get("to"),
            "date_added": message.get("dateAdded"),
            "recording_kind": recording_kind,
            "recording_ref": message.get("id") if recording_kind else None,
            "disposition": None,
        }

    if message_type == "TYPE_SMS":
        marketplace = ((message.get("meta") or {}).get("marketplace") or {})
        if marketplace.get("appName") == "JustCall":
            parsed = parse_justcall_body(message.get("body"))
            if parsed:
                attachments = message.get("attachments") or []
                recording_ref = parsed["recording_url"] or (attachments[0] if attachments else None)
                return {
                    "source": "justcall",
                    "is_voice_ai": False,
                    "conversation_id": conversation_id,
                    "message_id": message.get("id"),
                    "direction": parsed["direction"],
                    "status": parsed["status"],
                    "duration_seconds": parsed["duration_seconds"],
                    "agent_user_id": None,
                    "agent_name": parsed["assigned_to_name"],
                    "contact_id": message.get("contactId"),
                    "contact_name": contact_info.get("name"),
                    "contact_phone": contact_info.get("phone"),
                    "from_number": message.get("from"),
                    "to_number": message.get("to"),
                    "date_added": message.get("dateAdded"),
                    "recording_kind": "external" if recording_ref else None,
                    "recording_ref": recording_ref,
                    "disposition": parsed["disposition"],
                    "call_id": parsed["call_id"],
                    "missed_reason": parsed["missed_reason"],
                    "campaign": parsed["campaign"],
                }
    return None


def _log(msg):
    print(f"[calls_service] {msg}", file=sys.stderr, flush=True)


def scan_calls(client, start_dt, end_dt):
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    cache_key = (start_ms, end_ms)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > time.time():
            return cached[1], cached[2]

    calls, truncated = _scan_calls_uncached(client, start_ms, end_ms)

    with _cache_lock:
        _cache[cache_key] = (time.time() + CACHE_TTL_SECONDS, calls, truncated)
    return calls, truncated


def _scan_calls_uncached(client, start_ms, end_ms):
    t0 = time.time()

    touched_conversations = []
    start_after_date = None
    start_after = None
    truncated = False

    while True:
        data = client.search_conversations_page(
            limit=100, sort="desc", sort_by="last_message_date",
            start_after_date=start_after_date, start_after=start_after,
        )
        convs = data.get("conversations", [])
        if not convs:
            break

        stop = False
        for c in convs:
            last_msg_date = c.get("lastMessageDate")
            if last_msg_date is not None and last_msg_date < start_ms:
                stop = True
                break
            touched_conversations.append(c)

        if len(touched_conversations) >= SCAN_SAFETY_CAP:
            truncated = True
            break
        if stop:
            break

        last = convs[-1]
        sort_vals = last.get("sort")
        if not sort_vals:
            break
        start_after_date = sort_vals[0]
        start_after = last.get("id")

    t1 = time.time()
    _log(f"conversation scan: {len(touched_conversations)} conversations touched in range, "
         f"truncated={truncated}, took {t1 - t0:.1f}s")

    contact_info_by_conv = {
        c["id"]: {
            "name": c.get("contactName") or c.get("fullName"),
            "phone": c.get("phone"),
        }
        for c in touched_conversations
    }

    calls = []
    fetched = 0
    failed_conv_ids = []

    def fetch(conv_id):
        return conv_id, client.get_conversation_messages(conv_id, limit=100)

    with ThreadPoolExecutor(max_workers=MESSAGE_FETCH_WORKERS) as pool:
        futures = {pool.submit(fetch, c["id"]): c["id"] for c in touched_conversations}
        for future in as_completed(futures):
            conv_id = futures[future]
            try:
                conv_id, messages = future.result()
            except GHLAPIError as e:
                failed_conv_ids.append(conv_id)
                _log(f"FAILED to fetch messages for conversation {conv_id} after retries: {e}")
                continue
            fetched += 1
            for m in messages:
                ts_ms = _iso_to_ms(m.get("dateAdded"))
                if ts_ms is None or ts_ms < start_ms or ts_ms > end_ms:
                    continue
                record = _classify_message(m, conv_id, contact_info_by_conv.get(conv_id))
                if record:
                    calls.append(record)

    t2 = time.time()
    _log(f"message fetch: {fetched} ok, {len(failed_conv_ids)} failed, in {t2 - t1:.1f}s, "
         f"{len(calls)} call records found. total scan_calls time {t2 - t0:.1f}s")

    if failed_conv_ids:
        truncated = True

    return calls, truncated


ANSWERED_STATUSES = {"completed", "answered"}
MISSED_STATUSES = {"no-answer", "missed", "busy", "voicemail"}


def call_bucket_stats(items):
    total = len(items)
    answered = sum(1 for c in items if c["status"] in ANSWERED_STATUSES)
    missed = sum(1 for c in items if c["status"] in MISSED_STATUSES)
    inbound = sum(1 for c in items if c["direction"] == "inbound")
    outbound = sum(1 for c in items if c["direction"] == "outbound")
    duration = sum(c["duration_seconds"] or 0 for c in items)
    return {
        "total_calls": total,
        "answered_calls": answered,
        "missed_calls": missed,
        "inbound_calls": inbound,
        "outbound_calls": outbound,
        "other_status_calls": total - answered - missed,
        "total_duration_seconds": duration,
    }
