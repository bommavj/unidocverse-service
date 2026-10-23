"""Settings → Integrations → Web search (SearxNG). Admin only."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth_router import get_current_user, require_admin
from app.core.audit import record
from app.services import web_search

router = APIRouter(prefix="/api/web-search", tags=["web-search"])


class WebSearchSave(BaseModel):
    enabled: bool
    searxng_url: Optional[str] = None


@router.get("/settings")
def get_settings(_: dict = Depends(get_current_user)):
    return web_search.settings()


@router.put("/settings")
def put_settings(body: WebSearchSave, user=Depends(require_admin)):
    out = web_search.save_settings(body.enabled, body.searxng_url, user["username"])
    record("settings.web_search", actor=user["username"],
           detail=f"Web search {'on' if body.enabled else 'off'} ({out['searxng_url']})", extra=out)
    return out


@router.post("/test")
def test(body: WebSearchSave, _=Depends(require_admin)):
    try:
        res = web_search.search("internal revenue service", body.searxng_url)
    except Exception as e:
        raise HTTPException(502, f"Couldn't reach SearxNG: {e}. Check the URL and that JSON output is enabled "
                                 "(search.formats: [html, json] in settings.yml).")
    return {"ok": True, "results": len(res)}
