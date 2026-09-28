"""
Parses call summaries posted into GHL conversations by the JustCall marketplace
integration. These arrive as plain-text TYPE_SMS message bodies (JustCall does not
expose structured call fields through GHL's native message schema), for example:

    Missed Call
    Call ID: 145905458
    Date & Time: 17th Sep 2026, 06:09pm
    Call from: 07863 792041 ( +447863792041 )
    Received on: +447405385976
    Assigned To: himadhar Alahari
    Missed Call Reason: Call was not picked by any agent

    Outgoing Call (Answered)
    Call ID: 145901494
    Call Duration: 00h 00m 16s
    Date & Time: 17th Sep 2026, 05:58pm
    Called on:  ( +442083902848 )
    Called via: +447863792041
    Assigned To: anitha marry
    Campaign Name: Anitha_Evening_Campaign (#3369526)
    Call Recording: https://...
    Call Outcome (Disposition): Do Not Call
    Notes: customer not interested hungnup

This format is not an officially documented/guaranteed GHL schema - it is specific
to how the JustCall app happens to format its messages today. If JustCall changes
this template, parsing will need to be updated.
"""
import re

HEADER_RE = re.compile(r"^(Missed Call|Outgoing Call|Incoming Call)(?:\s*\(([^)]*)\))?", re.IGNORECASE)
DURATION_RE = re.compile(r"Call Duration:\s*(\d+)h\s*(\d+)m\s*(\d+)s", re.IGNORECASE)
ASSIGNED_RE = re.compile(r"Assigned To:\s*(.+)")
DISPOSITION_RE = re.compile(r"Call Outcome \(Disposition\):\s*(.+)")
CALL_ID_RE = re.compile(r"Call ID:\s*(\S+)")
RECORDING_RE = re.compile(r"Call Recording:\s*(\S+)")
MISSED_REASON_RE = re.compile(r"Missed Call Reason:\s*(.+)")
CAMPAIGN_RE = re.compile(r"Campaign Name:\s*(.+)")


def parse_justcall_body(body):
    """Returns a dict of parsed call fields, or None if this text doesn't match
    a known JustCall call-log template (e.g. it's an ordinary SMS)."""
    if not body:
        return None
    header = HEADER_RE.match(body.strip())
    if not header:
        return None

    call_kind = header.group(1).lower()
    outcome_paren = (header.group(2) or "").strip()

    if call_kind == "missed call":
        direction = "inbound"
        status = "missed"
    elif call_kind == "incoming call":
        direction = "inbound"
        status = "answered" if outcome_paren.lower() == "answered" else (outcome_paren.lower() or "unknown")
    else:  # outgoing call
        direction = "outbound"
        status = "answered" if outcome_paren.lower() == "answered" else (outcome_paren.lower() or "unknown")

    duration_seconds = None
    dur_match = DURATION_RE.search(body)
    if dur_match:
        h, m, s = (int(x) for x in dur_match.groups())
        duration_seconds = h * 3600 + m * 60 + s

    assigned_match = ASSIGNED_RE.search(body)
    disposition_match = DISPOSITION_RE.search(body)
    call_id_match = CALL_ID_RE.search(body)
    recording_match = RECORDING_RE.search(body)
    missed_reason_match = MISSED_REASON_RE.search(body)
    campaign_match = CAMPAIGN_RE.search(body)

    return {
        "call_kind": call_kind,
        "direction": direction,
        "status": status,
        "duration_seconds": duration_seconds,
        "assigned_to_name": assigned_match.group(1).strip() if assigned_match else None,
        "disposition": disposition_match.group(1).strip() if disposition_match else None,
        "call_id": call_id_match.group(1).strip() if call_id_match else None,
        "recording_url": recording_match.group(1).strip() if recording_match else None,
        "missed_reason": missed_reason_match.group(1).strip() if missed_reason_match else None,
        "campaign": campaign_match.group(1).strip() if campaign_match else None,
    }
