"""Builds the Sales & Opportunities table and stage-aggregation chart data.

GHL's opportunities/search endpoint does not return stageAggregations when
filters are applied (verified empirically), so per-stage totals are computed
by issuing one bounded count query per pipeline stage instead.
"""


def build_pipeline_lookup(pipelines):
    pipeline_by_id = {p["id"]: p for p in pipelines}
    stage_by_id = {}
    for p in pipelines:
        for s in p.get("stages", []):
            stage_by_id[s["id"]] = {"name": s["name"], "pipeline_id": p["id"], "pipeline_name": p["name"]}
    return pipeline_by_id, stage_by_id


def list_opportunities(client, start_dt, end_dt, users_by_id, pipeline_by_id, stage_by_id,
                        agent_id=None, pipeline_id=None, status=None, limit=200):
    filters = [{
        "field": "date_added",
        "operator": "range",
        "value": {"gte": start_dt.strftime("%Y-%m-%d"), "lte": end_dt.strftime("%Y-%m-%d")},
    }]
    if agent_id:
        filters.append({"field": "assigned_to", "operator": "eq", "value": agent_id})
    if pipeline_id:
        filters.append({"field": "pipeline_id", "operator": "eq", "value": pipeline_id})
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


def sales_by_stage(client, start_dt, end_dt, pipelines):
    date_filter = {
        "field": "date_added",
        "operator": "range",
        "value": {"gte": start_dt.strftime("%Y-%m-%d"), "lte": end_dt.strftime("%Y-%m-%d")},
    }
    result = []
    for p in pipelines:
        for s in p.get("stages", []):
            count = client.count_opportunities(filters=[
                date_filter,
                {"field": "pipeline_stage_id", "operator": "eq", "value": s["id"]},
            ])
            if count:
                result.append({"pipeline_name": p["name"], "stage_name": s["name"], "count": count})
    return result
