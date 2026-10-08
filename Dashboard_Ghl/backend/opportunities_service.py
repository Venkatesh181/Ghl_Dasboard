"""Builds the Sales & Opportunities table and stage-aggregation chart data."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

STATUSES = ["open", "won", "lost", "abandoned"]
CACHE_TTL = 300  # 5 minutes in-memory cache

_stage_cache = {}
_summary_cache = {}
_cache_lock = threading.Lock()


def build_pipeline_lookup(pipelines):
    pipeline_by_id = {p["id"]: p for p in pipelines}
    stage_by_id = {}
    for p in pipelines:
        for s in p.get("stages", []):
            stage_by_id[s["id"]] = {"name": s["name"], "pipeline_id": p["id"], "pipeline_name": p["name"]}
    return pipeline_by_id, stage_by_id


def list_opportunities(client, start_dt, end_dt, users_by_id, pipeline_by_id, stage_by_id,
                        agent_id=None, pipeline_id=None, stage_id=None, status=None, limit=200):
    filters = [{
        "field": "date_added",
        "operator": "range",
        "value": {"gte": start_dt.strftime("%Y-%m-%d"), "lte": end_dt.strftime("%Y-%m-%d")},
    }]
    if agent_id:
        filters.append({"field": "assigned_to", "operator": "eq", "value": agent_id})
    if pipeline_id:
        filters.append({"field": "pipeline_id", "operator": "eq", "value": pipeline_id})
    if stage_id:
        filters.append({"field": "pipeline_stage_id", "operator": "eq", "value": stage_id})
    if status:
        filters.append({"field": "status", "operator": "eq", "value": status})

    data = client.search_opportunities_page(filters=filters, limit=limit)
    total = data.get("total", 0)
    rows = []
    for o in data.get("opportunities", []):
        agent = users_by_id.get(o.get("assignedTo"))
        stage = stage_by_id.get(o.get("pipelineStageId"), {})
        pipeline = pipeline_by_id.get(o.get("pipelineId"))
        contact = o.get("contact") or {}
        rows.append({
            "id": o.get("id"),
            "name": o.get("name"),
            "customer_name": contact.get("name"),
            "agent_name": (agent.get("name") if agent else None) or "Unassigned",
            "pipeline_name": pipeline.get("name") if pipeline else stage.get("pipeline_name") or "Unknown",
            "stage_name": stage.get("name") or "Unknown",
            "status": o.get("status"),
            "value": o.get("monetaryValue") or 0,
            "created_at": o.get("createdAt"),
            "updated_at": o.get("updatedAt"),
        })
    return rows, total


def opportunity_status_summary(client, agent_id=None, pipeline_id=None, stage_id=None):
    """Instant summary lookup with memory cache and fast single-query lookups."""
    cache_key = (agent_id, pipeline_id, stage_id)
    now = time.time()
    with _cache_lock:
        if cache_key in _summary_cache and _summary_cache[cache_key]["exp"] > now:
            return _summary_cache[cache_key]["data"]

    base_filters = []
    if agent_id:
        base_filters.append({"field": "assigned_to", "operator": "eq", "value": agent_id})
    if pipeline_id:
        base_filters.append({"field": "pipeline_id", "operator": "eq", "value": pipeline_id})
    if stage_id:
        base_filters.append({"field": "pipeline_stage_id", "operator": "eq", "value": stage_id})

    # Fast parallel count for each status (1 request per status instead of 5000-page loops)
    def fetch_status_stat(status):
        st_filters = base_filters + [{"field": "status", "operator": "eq", "value": status}]
        data = client.search_opportunities_page(filters=st_filters, limit=100)
        total_count = data.get("total", 0)
        sample = data.get("opportunities", [])
        
        # Calculate sample value and scale
        sample_value = sum(float(o.get("monetaryValue") or 0) for o in sample)
        sample_contacts = len({o.get("contactId") for o in sample if o.get("contactId")})
        
        # If total fits in sample, exact match; otherwise extrapolate safely
        if total_count <= len(sample):
            total_val = sample_value
            contact_count = sample_contacts
        else:
            avg_val = (sample_value / len(sample)) if sample else 0
            total_val = round(avg_val * total_count, 2)
            contact_count = total_count

        return status, {
            "count": total_count,
            "contact_count": contact_count,
            "value": round(total_val, 2),
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(fetch_status_stat, STATUSES))

    closed_count = results["won"]["count"] + results["lost"]["count"] + results["abandoned"]["count"]
    closed_val = round(results["won"]["value"] + results["lost"]["value"] + results["abandoned"]["value"], 2)
    closed_contacts = results["won"]["contact_count"] + results["lost"]["contact_count"] + results["abandoned"]["contact_count"]

    res = {
        "open": results["open"],
        "closed": {
            "count": closed_count,
            "contact_count": closed_contacts,
            "value": closed_val,
        },
        "won": results["won"],
        "lost": results["lost"],
        "abandoned": results["abandoned"],
        "total_income": results["won"]["value"],
    }

    with _cache_lock:
        _summary_cache[cache_key] = {"exp": now + CACHE_TTL, "data": res}
    return res


def sales_by_stage(client, pipelines, agent_id=None, pipeline_id=None):
    """Threaded stage count with 5-minute cache to prevent duplicate multi-request bursts."""
    cache_key = (agent_id, pipeline_id)
    now = time.time()
    with _cache_lock:
        if cache_key in _stage_cache and _stage_cache[cache_key]["exp"] > now:
            return _stage_cache[cache_key]["data"]

    if pipeline_id:
        pipelines = [p for p in pipelines if p["id"] == pipeline_id]

    tasks = []
    for p in pipelines:
        for s in sorted(p.get("stages", []), key=lambda x: x.get("position", 0)):
            tasks.append((p["id"], p["name"], s["id"], s["name"]))

    def fetch_count(task):
        p_id, p_name, s_id, s_name = task
        filters = [{"field": "pipeline_stage_id", "operator": "eq", "value": s_id}]
        if agent_id:
            filters.append({"field": "assigned_to", "operator": "eq", "value": agent_id})
        count = client.count_opportunities(filters=filters)
        return {"pipeline_id": p_id, "pipeline_name": p_name, "stage_name": s_name, "count": count}

    with ThreadPoolExecutor(max_workers=10) as pool:
        result = list(pool.map(fetch_count, tasks))

    with _cache_lock:
        _stage_cache[cache_key] = {"exp": now + CACHE_TTL, "data": result}
    return result