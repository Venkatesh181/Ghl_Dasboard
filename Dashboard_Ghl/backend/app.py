import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from ghl_client import GHLClient, GHLConfigError, GHLAPIError
from calls_service import scan_calls, call_bucket_stats
from agents_service import compute_agent_performance, build_name_lookup, match_agent_by_name
from opportunities_service import build_pipeline_lookup, list_opportunities, sales_by_stage, opportunity_status_summary
from appointments_service import list_appointments
from charts_service import calls_over_time, calls_by_status

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
OPPORTUNITY_STATUSES = ["open", "won", "lost", "abandoned"]
ALLOWED_RECORDING_HOSTS = ("justcall.io", "amazonaws.com", "leadconnectorhq.com")


def _host_allowed(hostname):
    if not hostname:
        return False
    hostname = hostname.lower()
    return any(hostname == h or hostname.endswith("." + h) for h in ALLOWED_RECORDING_HOSTS)

app = Flask(__name__, static_folder=None)


def get_client():
    return GHLClient()


def parse_date_range():
    today = datetime.now(timezone.utc).date()
    start_str = request.args.get("start")
    end_str = request.args.get("end")
    end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else today
    start_date = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else end_date

    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, 999000, tzinfo=timezone.utc)
    return start_dt, end_dt


def users_by_id_map(client):
    return {u["id"]: u for u in client.list_users()}


def error_response(e):
    if isinstance(e, GHLConfigError):
        return jsonify({"error": "config_error", "message": str(e)}), 500
    if isinstance(e, GHLAPIError):
        return jsonify({
            "error": "ghl_api_error",
            "status_code": e.status_code,
            "endpoint": e.endpoint,
            "message": str(e),
        }), 502
    return jsonify({"error": "server_error", "message": str(e)}), 500


@app.route("/api/health")
def health():
    try:
        client = get_client()
        masked = client.token[:4] + "..." + client.token[-4:]
        return jsonify({"status": "ok", "location_id": client.location_id, "token_masked": masked})
    except Exception as e:
        return error_response(e)


@app.route("/api/agents")
def agents():
    try:
        client = get_client()
        users = client.list_users()
        return jsonify({
            "agents": [
                {"id": u["id"], "name": u.get("name") or f"{u.get('firstName', '')} {u.get('lastName', '')}".strip()}
                for u in users if not u.get("deleted")
            ]
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/pipelines")
def pipelines():
    try:
        client = get_client()
        pls = client.list_pipelines()
        return jsonify({
            "pipelines": [
                {"id": p["id"], "name": p["name"], "stages": [{"id": s["id"], "name": s["name"]} for s in p.get("stages", [])]}
                for p in pls
            ],
            "opportunity_statuses": OPPORTUNITY_STATUSES,
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/overview")
def overview():
    try:
        client = get_client()
        start_dt, end_dt = parse_date_range()

        total_contacts = client.count_contacts()
        total_opportunities = client.count_opportunities()

        opp_date_filter = [{
            "field": "date_added",
            "operator": "range",
            "value": {"gte": start_dt.strftime("%Y-%m-%d"), "lte": end_dt.strftime("%Y-%m-%d")},
        }]
        won_filters = opp_date_filter + [{"field": "status", "operator": "eq", "value": "won"}]
        won_data = client.search_opportunities_page(filters=won_filters, limit=100)
        won_opps = won_data.get("opportunities", [])
        total_sales_value = sum(o.get("monetaryValue") or 0 for o in won_opps)
        won_count = won_data.get("total", len(won_opps))

        pipeline_list = client.list_pipelines()
        qualified_stage_ids = [
            s["id"] for p in pipeline_list for s in p.get("stages", [])
            if "qualif" in s.get("name", "").strip().lower()
        ]
        qualified_total = 0
        for stage_id in qualified_stage_ids:
            qualified_total += client.count_opportunities(filters=[
                {"field": "pipeline_stage_id", "operator": "eq", "value": stage_id}
            ])

        calls, truncated = scan_calls(client, start_dt, end_dt)
        native_calls = [c for c in calls if c["source"] == "native" and not c["is_voice_ai"]]
        voice_ai_calls = [c for c in calls if c["is_voice_ai"]]
        justcall_calls = [c for c in calls if c["source"] == "justcall"]
        all_calls = native_calls + voice_ai_calls + justcall_calls

        return jsonify({
            "date_range": {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
            "contacts": {"total": total_contacts},
            "opportunities": {"total": total_opportunities},
            "sales": {"total_value": round(total_sales_value, 2), "won_count": won_count},
            "qualified_appointments": {"total": qualified_total},
            "calls": {
                "combined": call_bucket_stats(all_calls),
                "native": call_bucket_stats(native_calls),
                "justcall": call_bucket_stats(justcall_calls),
                "voice_ai": call_bucket_stats(voice_ai_calls),
                "scan_truncated": truncated,
            },
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/agent-performance")
def agent_performance():
    try:
        client = get_client()
        start_dt, end_dt = parse_date_range()
        agent_id_filter = request.args.get("agentId") or None

        users = client.list_users()
        calls, truncated = scan_calls(client, start_dt, end_dt)
        rows = compute_agent_performance(calls, users, agent_id_filter=agent_id_filter)

        return jsonify({
            "date_range": {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
            "rows": rows,
            "scan_truncated": truncated,
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/call-logs")
def call_logs():
    try:
        client = get_client()
        start_dt, end_dt = parse_date_range()
        agent_id = request.args.get("agentId") or None
        direction = request.args.get("direction") or None
        status = request.args.get("status") or None
        search = (request.args.get("search") or "").strip().lower()

        users = client.list_users()
        id_to_user = {u["id"]: u for u in users}
        name_lookup = build_name_lookup(users)

        calls, truncated = scan_calls(client, start_dt, end_dt)

        rows = []
        for c in calls:
            if c["source"] == "native":
                user = id_to_user.get(c.get("agent_user_id"))
                agent_name = (user.get("name") if user else None) or "Unassigned"
            else:
                matched = match_agent_by_name(c.get("agent_name"), name_lookup)
                agent_name = matched.get("name") if matched else (f"Unmatched: {c.get('agent_name')}" if c.get("agent_name") else "Unknown")

            if agent_id:
                real_agent_id = c.get("agent_user_id") or (match_agent_by_name(c.get("agent_name"), name_lookup) or {}).get("id")
                if real_agent_id != agent_id:
                    continue
            if direction and c["direction"] != direction:
                continue
            if status and c["status"] != status:
                continue
            if search:
                haystack = f"{c.get('contact_name') or ''} {c.get('contact_phone') or ''} {c.get('from_number') or ''} {c.get('to_number') or ''}".lower()
                if search not in haystack:
                    continue

            rows.append({
                "date_added": c.get("date_added"),
                "agent_name": agent_name,
                "contact_name": c.get("contact_name") or "Unknown",
                "phone_number": c.get("contact_phone") or c.get("from_number") or c.get("to_number"),
                "direction": c.get("direction") or "unknown",
                "status": c.get("status") or "unknown",
                "duration_seconds": c.get("duration_seconds"),
                "has_recording": c.get("recording_kind") is not None,
                "recording_kind": c.get("recording_kind"),
                "recording_ref": c.get("recording_ref"),
                "source": c["source"],
            })

        rows.sort(key=lambda r: r["date_added"] or "", reverse=True)
        return jsonify({
            "date_range": {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
            "rows": rows,
            "scan_truncated": truncated,
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/opportunities")
def opportunities():
    try:
        client = get_client()
        start_dt, end_dt = parse_date_range()
        agent_id = request.args.get("agentId") or None
        pipeline_id = request.args.get("pipelineId") or None
        stage_id = request.args.get("stageId") or None
        status = request.args.get("status") or None
        search = (request.args.get("search") or "").strip().lower()

        users = users_by_id_map(client)
        pipeline_list = client.list_pipelines()
        pipeline_by_id, stage_by_id = build_pipeline_lookup(pipeline_list)

        rows, total = list_opportunities(
            client, start_dt, end_dt, users, pipeline_by_id, stage_by_id,
            agent_id=agent_id, pipeline_id=pipeline_id, stage_id=stage_id, status=status, limit=200,
        )

        if search:
            rows = [r for r in rows if search in (r.get("customer_name") or "").lower() or search in (r.get("name") or "").lower()]

        return jsonify({
            "date_range": {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
            "rows": rows,
            "total": total,
            "returned": len(rows),
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/opportunities/summary")
def opportunities_summary():
    try:
        client = get_client()
        agent_id = request.args.get("agentId") or None
        pipeline_id = request.args.get("pipelineId") or None
        stage_id = request.args.get("stageId") or None

        summary = opportunity_status_summary(client, agent_id=agent_id, pipeline_id=pipeline_id, stage_id=stage_id)
        pipeline_list = client.list_pipelines()
        by_stage = sales_by_stage(client, pipeline_list, agent_id=agent_id, pipeline_id=pipeline_id)

        return jsonify({
            "by_stage": by_stage,
            **summary,
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/appointments")
def appointments():
    try:
        client = get_client()
        start_dt, end_dt = parse_date_range()
        agent_id = request.args.get("agentId") or None
        status = request.args.get("status") or None

        users = users_by_id_map(client)
        rows = list_appointments(client, start_dt, end_dt, users, agent_id=agent_id, status=status)
        return jsonify({
            "date_range": {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
            "rows": rows,
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/charts/overview")
def charts_overview():
    try:
        client = get_client()
        start_dt, end_dt = parse_date_range()
        agent_id = request.args.get("agentId") or None
        pipeline_id = request.args.get("pipelineId") or None

        calls, truncated = scan_calls(client, start_dt, end_dt)
        users = client.list_users()
        agent_rows = compute_agent_performance(calls, users)
        pipeline_list = client.list_pipelines()
        by_stage = sales_by_stage(client, pipeline_list, agent_id=agent_id, pipeline_id=pipeline_id)

        return jsonify({
            "calls_over_time": calls_over_time(calls),
            "calls_by_status": calls_by_status(calls),
            "calls_by_agent": [{"agent_name": r["agent_name"], "total_calls": r["total_calls"]} for r in agent_rows[:10]],
            "sales_by_stage": by_stage,
            "scan_truncated": truncated,
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/recording-proxy")
def recording_proxy():
    message_id = request.args.get("messageId")
    url = request.args.get("url")
    range_header = request.headers.get("Range")

    if message_id:
        try:
            client = get_client()
            upstream = client.stream_message_recording(message_id, range_header=range_header)
        except GHLConfigError as e:
            return jsonify({"error": "config_error", "message": str(e)}), 500
    elif url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not _host_allowed(parsed.hostname):
            return jsonify({"error": "host_not_allowed"}), 400
        headers = {"Range": range_header} if range_header else {}
        upstream = requests.get(url, headers=headers, stream=True, timeout=30)
    else:
        return jsonify({"error": "missing_params"}), 400

    if upstream.status_code >= 400:
        status = upstream.status_code
        upstream.close()
        return jsonify({"error": "upstream_error", "status_code": status}), status

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    headers = {"Content-Type": upstream.headers.get("Content-Type", "audio/mpeg")}
    return Response(stream_with_context(generate()), status=upstream.status_code, headers=headers)


@app.route("/")
@app.route("/<path:path>")
def frontend(path="index.html"):
    return send_from_directory(FRONTEND_DIR, path)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)