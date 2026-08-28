"""Журнал действий админов (ТЗ п.38)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import deps
from app.admin.templating import render
from app.core.security import Perm
from app.services import audit

router = APIRouter(tags=["admin-logs"])

AuditViewer = Depends(deps.require_perm(Perm.audit))
PER_PAGE = 50


def _parse_date(value: str) -> dt.datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


@router.get("/logs", response_class=HTMLResponse)
async def logs_list(request: Request, db: deps.Db, admin=AuditViewer) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, PER_PAGE)
    params = request.query_params
    entity = params.get("entity", "").strip() or None
    action = params.get("action", "").strip() or None
    query = params.get("q", "").strip() or None
    date_from = _parse_date(params.get("from", ""))
    date_to = _parse_date(params.get("to", ""))

    rows, total = await audit.list_logs(
        db,
        entity=entity,
        action=action,
        query=query,
        date_from=date_from,
        date_to=date_to,
        limit=per_page,
        offset=offset,
    )
    return await render(
        request,
        "logs/list.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "entity": entity or "",
            "action": action or "",
            "query": query or "",
            "date_from": params.get("from", ""),
            "date_to": params.get("to", ""),
            "entities": await audit.distinct_entities(db),
            "actions": await audit.distinct_actions(db),
        },
        db=db,
    )
