"""
Document Research Agent Router
POST /api/research/ask        — run ReAct agent on a question
GET  /api/research/ask/stream — SSE streaming version
GET  /api/research/docs       — list documents available for research
GET  /api/research/history    — list past research sessions (newest first)
"""
import json
import logging
import re
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.services.document_react_agent import run_research_agent, _do_execute_action, _synthesize_stream
from app.services.supervisor_agent import run_supervisor, classify_question, classify_intent
from app.agents.query_workflow import run_query_workflow
from app.api.auth_router import get_current_user

logger = logging.getLogger(__name__)


def _decode_unicode_escapes(text: str) -> str:
    """Decode literal \\uXXXX sequences emitted by some LLM outputs as plain text."""
    if not text or '\\u' not in text:
        return text
    try:
        return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), text)
    except Exception:
        return text


def _fmt_val(v: float) -> str:
    """Format a number: no decimals if whole, 2 decimals otherwise."""
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"


def _chart_summary(chart_data: dict, question: str) -> str:
    """Generate a concise text summary from pre-built chart data so the LLM isn't needed."""
    labels = chart_data.get("labels") or []
    datasets = chart_data.get("datasets") or []
    if not labels or not datasets:
        return "Here is a visualization of the requested data."

    values = datasets[0].get("data") or []
    metric = datasets[0].get("label", "Value")
    group_col = chart_data.get("_group_col", "")

    if not labels or not values:
        return "Here is a visualization of the requested data."

    paired = list(zip(labels, values))
    top3 = paired[:3]
    total = sum(v for _, v in paired if isinstance(v, (int, float)))
    is_currency = any(kw in metric.lower() for kw in ["$", "price", "sales", "revenue", "amount", "cost", "salary"])
    prefix = "$" if is_currency else ""

    lines = [f"Here is the **{metric}** breakdown grouped by **{group_col or 'category'}**:\n"]
    for label, val in top3:
        pct = f" ({val/total*100:.1f}%)" if total > 0 else ""
        lines.append(f"- **{label}**: {prefix}{_fmt_val(val)}{pct}")
    if len(paired) > 3:
        lines.append(f"- … and {len(paired) - 3} more categories shown in the chart.")
    lines.append(f"\n**Total across all categories:** {prefix}{_fmt_val(total)}")
    return "\n".join(lines)


def _save_session(result: dict, doc_id_list: list, question: str, parent_session_id: str = None) -> str | None:
    """Persist a completed research session to research_sessions table. Returns session id."""
    try:
        db = SessionLocal()
        chart_data = result.get("chart_data")
        from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
        tenant_id = get_current_tenant_id() or DEFAULT_TENANT_ID
        row = db.execute(text("""
            INSERT INTO research_sessions
                (question, answer, sources, steps, iterations, doc_ids, chart_data, matched_doc_ids, parent_session_id, tenant_id)
            VALUES
                (:question, :answer, :sources, CAST(:steps AS JSONB), :iterations, :doc_ids,
                 CAST(:chart_data AS JSONB), :matched_doc_ids, :parent_session_id, :tenant_id)
            RETURNING id
        """), {
            "question":          result.get("question", question),
            "answer":            _decode_unicode_escapes(result.get("answer", "")),
            "sources":           result.get("sources") or [],
            "steps":             json.dumps(result.get("steps") or []),
            "iterations":        result.get("iterations", 0),
            "doc_ids":           doc_id_list or [],
            "chart_data":        json.dumps(chart_data) if chart_data else None,
            "matched_doc_ids":   result.get("matched_doc_ids") or [],
            "parent_session_id": parent_session_id or None,
            "tenant_id":         tenant_id,
        }).fetchone()
        db.commit()
        session_id = str(row.id) if row else None
        db.close()
        return session_id
    except Exception as e:
        logger.warning(f"Failed to save stream session: {e}")
        return None

router = APIRouter(prefix="/api/research", tags=["research"])


class ResearchRequest(BaseModel):
    question: str
    doc_ids: Optional[List[str]] = None  # empty = search all docs
    page: int = 0                         # 0-based page for chunk pagination


@router.post("/ask")
def research_ask(body: ResearchRequest, db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    if not body.question or not body.question.strip():
        raise HTTPException(400, "Question is required")
    try:
        result = run_research_agent(
            question=body.question.strip(),
            doc_ids=body.doc_ids or [],
            page=body.page or 0,
        )
        # Persist the session for history
        session_id = None
        try:
            import json as _json
            row = db.execute(text("""
                INSERT INTO research_sessions
                    (question, answer, sources, steps, iterations, doc_ids)
                VALUES
                    (:question, :answer, :sources, CAST(:steps AS JSONB), :iterations, :doc_ids)
                RETURNING id
            """), {
                "question":   result.get("question", body.question.strip()),
                "answer":     result.get("answer", ""),
                "sources":    result.get("sources") or [],
                "steps":      _json.dumps(result.get("steps") or []),
                "iterations": result.get("iterations", 0),
                "doc_ids":    body.doc_ids or [],
            }).fetchone()
            db.commit()
            session_id = str(row.id) if row else None
        except Exception as save_err:
            logger.warning(f"Failed to save research session: {save_err}")
            db.rollback()
        return {**result, "session_id": session_id}
    except Exception as e:
        logger.error(f"Research agent error: {e}", exc_info=True)
        raise HTTPException(500, f"Research agent failed: {str(e)}")


@router.get("/ask/stream")
def research_ask_stream(
    question: str = Query(...),
    doc_ids: Optional[str] = Query(None),
    page: int = Query(0),
    context_doc_ids: Optional[str] = Query(None),
    prior_qa: Optional[str] = Query(None),
    parent_session_id: Optional[str] = Query(None),
    _: dict = Depends(get_current_user),
):
    """SSE endpoint — streams step events then answer tokens."""
    if not question or not question.strip():
        raise HTTPException(400, "Question is required")

    doc_id_list     = [d for d in (doc_ids         or "").split(",") if d.strip()]
    ctx_doc_id_list = [d for d in (context_doc_ids or "").split(",") if d.strip()]
    prior_qa_list: list = []
    if prior_qa:
        try:
            prior_qa_list = json.loads(prior_qa)
        except Exception:
            prior_qa_list = []

    def event_stream():
        try:
            # Signal immediately so the frontend shows the typing indicator
            yield f"data: {json.dumps({'type': 'thinking'})}\n\n"

            # ── Intent-based routing ──────────────────────────────────────────
            # classify_intent() is <1ms — pure keyword heuristic, no LLM call.
            #
            #  simple / fast  → run_research_agent (single LLM call, pgvector only)
            #                   Best for: "what is my balance?", "explain this clause"
            #                   Latency: ~2-4s
            #
            #  analytical / langgraph → run_query_workflow (Supervisor + domain agents)
            #                   Best for: "compare spending across all merchants this quarter"
            #                   Latency: ~8-20s depending on domain count
            #
            intent = classify_intent(question.strip(), doc_id_list)
            logger.info(
                f"[research_router] intent={intent.route} "
                f"complexity={intent.complexity} reason='{intent.reason}'"
            )

            # Emit intent metadata so the frontend can optionally show it
            yield f"data: {json.dumps({'type': 'intent', 'route': intent.route, 'complexity': intent.complexity})}\n\n"

            if intent.route == "clarify":
                # Vague question — create a HITL approval request for clarification
                import uuid as _uuid
                _approval_id = str(_uuid.uuid4())
                _db_hitl = None
                try:
                    from app.core.database import SessionLocal as _SL
                    from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
                    _db_hitl = _SL()
                    _tid = get_current_tenant_id() or DEFAULT_TENANT_ID
                    _db_hitl.execute(text("""
                        INSERT INTO approval_requests
                            (id, doc_id, title, description, approvers, status, priority)
                        VALUES
                            (:id, '', :title, :desc, '["admin"]'::jsonb, 'pending', 'high')
                    """), {
                        "id":    _approval_id,
                        "title": f"Clarification needed: \"{question.strip()[:80]}\"",
                        "desc":  (
                            f"The research agent received a vague query and needs clarification "
                            f"before proceeding.\n\n"
                            f"**Original question:** {question.strip()}\n\n"
                            f"Please clarify:\n"
                            f"- What type of documents are you asking about? "
                            f"(bank statements, insurance policies, pay stubs, contracts, etc.)\n"
                            f"- What specifically would you like to know?"
                        ),
                    })
                    _db_hitl.commit()
                except Exception as _he:
                    logger.warning(f"HITL insert failed: {_he}")
                finally:
                    if _db_hitl:
                        _db_hitl.close()

                _hitl_msg = (
                    "⏳ **Clarification needed** — your question has been sent to the approval queue.\n\n"
                    "To get an accurate answer, please clarify:\n"
                    "- **What type of documents** are you asking about? "
                    "(e.g. bank statements, insurance policies, pay stubs, contracts)\n"
                    "- **What specifically** would you like to know?\n\n"
                    "Or rephrase your question with more detail and try again."
                )
                for word in _hitl_msg.split(" "):
                    yield f"data: {json.dumps({'type': 'token', 'text': word + ' '})}\n\n"
                    time.sleep(0.012)
                yield f"data: {json.dumps({'type': 'hitl', 'approval_id': _approval_id})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'session_id': None, 'matched_doc_ids': []})}\n\n"
                return

            elif intent.route == "fast":
                # Lightweight: single vector search + one LLM call, no agents
                result = run_research_agent(
                    question=question.strip(),
                    doc_ids=doc_id_list,
                    page=page,
                    prior_qa=prior_qa_list or None,
                )
            else:
                # Full LangGraph: Supervisor → domain agents → merger → synthesizer
                result = run_query_workflow(
                    question=question.strip(),
                    doc_ids=doc_id_list,
                )

            # Emit step events
            for step in (result.get("steps") or []):
                yield f"data: {json.dumps({'type': 'step', 'step': step})}\n\n"

            # If the pipeline already produced a direct answer (e.g. query_transactions SQL path)
            # stream it token by token without calling _synthesize_stream
            direct_answer = result.get("answer")
            if direct_answer:
                direct_answer = _decode_unicode_escapes(direct_answer)
                for word in direct_answer.split(" "):
                    yield f"data: {json.dumps({'type': 'token', 'text': word + ' '})}\n\n"
                    time.sleep(0.018)
                sources = result.get("sources") or []
                if sources:
                    yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
                chart_data = result.get("chart_data")
                if chart_data:
                    yield f"data: {json.dumps({'type': 'chart', 'chart': chart_data})}\n\n"
                matched_doc_ids = result.get("matched_doc_ids") or doc_id_list
                session_id = _save_session(result, doc_id_list, question.strip(), parent_session_id)
                yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'matched_doc_ids': matched_doc_ids, 'has_more': result.get('has_more', False), 'page': result.get('page', 0)})}\n\n"
                return

            # Otherwise stream synthesis from vector search chunks
            plan = result.get("_plan") or {}
            steps = result.get("steps") or []
            chart_data = result.get("chart_data")
            synth_sources = []

            if chart_data:
                # Chart was pre-built from chunks — emit a text summary instead of
                # running the LLM (which would return NOT FOUND on raw tabular data).
                summary = _decode_unicode_escapes(_chart_summary(chart_data, question.strip()))
                for word in summary.split(" "):
                    yield f"data: {json.dumps({'type': 'token', 'text': word + ' '})}\n\n"
                    time.sleep(0.018)
                # Collect sources from chunks
                seen = set()
                for s in steps:
                    obs = s.get("observation") or {}
                    chunks = obs if isinstance(obs, list) else (obs.get("chunks") or obs.get("results") or [])
                    for ch in chunks:
                        fn = ch.get("filename", "")
                        if fn and fn not in seen:
                            seen.add(fn); synth_sources.append(fn)
                if synth_sources:
                    yield f"data: {json.dumps({'type': 'sources', 'sources': synth_sources})}\n\n"
                yield f"data: {json.dumps({'type': 'chart', 'chart': chart_data})}\n\n"
            else:
                # Stream tokens as they arrive — apply unicode decode per-token.
                # \uXXXX sequences are always 6 chars and never span token boundaries
                # (Ollama emits them as atomic units), so per-token decode is safe.
                for kind, payload in _synthesize_stream(question.strip(), plan, steps, page, prior_qa=prior_qa_list or None):
                    if kind == "token":
                        clean = _decode_unicode_escapes(payload)
                        yield f"data: {json.dumps({'type': 'token', 'text': clean})}\n\n"
                    elif kind == "sources":
                        synth_sources = payload
                        yield f"data: {json.dumps({'type': 'sources', 'sources': payload})}\n\n"

            saved_answer = summary if chart_data else ""
            matched_doc_ids = result.get("matched_doc_ids") or doc_id_list
            session_id = _save_session({**result, "sources": synth_sources, "answer": saved_answer}, doc_id_list, question.strip(), parent_session_id)
            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'matched_doc_ids': matched_doc_ids})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@router.get("/classify")
def classify_endpoint(question: str = Query(...)):
    """Debug endpoint — shows how the supervisor would route a question."""
    domains = classify_question(question)
    intent = classify_intent(question)
    from app.services.supervisor_agent import DOMAINS
    return {
        "question":   question,
        "routed_to":  domains,
        "labels":     [DOMAINS[d]["label"] for d in domains],
        "intent":     intent.route,
        "complexity": intent.complexity,
        "reason":     intent.reason,
    }


@router.get("/history")
def research_history(limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    total = db.execute(text("SELECT COUNT(*) FROM research_sessions WHERE parent_session_id IS NULL")).scalar() or 0

    rows = db.execute(text("""
        SELECT id, question, answer, sources, steps, iterations, doc_ids,
               chart_data, matched_doc_ids, created_at
        FROM research_sessions
        WHERE parent_session_id IS NULL
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """), {"limit": safe_limit, "offset": safe_offset}).fetchall()

    root_ids = [str(r.id) for r in rows]

    # Fetch all follow-up turns for these root sessions in one query
    thread_map: dict = {rid: [] for rid in root_ids}
    if root_ids:
        placeholders = ", ".join(f"'{rid}'" for rid in root_ids)
        child_rows = db.execute(text(f"""
            SELECT id, parent_session_id, question, answer, sources, chart_data, matched_doc_ids, created_at
            FROM research_sessions
            WHERE parent_session_id IN ({placeholders})
            ORDER BY created_at ASC
        """)).fetchall()
        for cr in child_rows:
            pid = str(cr.parent_session_id)
            if pid in thread_map:
                thread_map[pid].append({
                    "question":       cr.question,
                    "answer":         cr.answer or "",
                    "sources":        cr.sources or [],
                    "chartData":      cr.chart_data,
                    "matchedDocIds":  cr.matched_doc_ids or [],
                })

    items = [
        {
            "id":              str(r.id),
            "question":        r.question,
            "answer":          r.answer,
            "sources":         r.sources or [],
            "steps":           r.steps or [],
            "iterations":      r.iterations,
            "doc_ids":         r.doc_ids or [],
            "chart_data":      r.chart_data,        # JSONB — already a dict
            "matched_doc_ids": r.matched_doc_ids or [],
            "ts":              r.created_at.isoformat(),
            "thread":          thread_map.get(str(r.id), []),
        }
        for r in rows
    ]
    return {
        "items":    items,
        "total":    total,
        "limit":    safe_limit,
        "offset":   safe_offset,
        "has_more": (safe_offset + len(items)) < total,
    }


@router.get("/session/{session_id}")
def get_research_session(session_id: str, db: Session = Depends(get_db)):
    """Return a single root session with its full thread (child follow-ups)."""
    row = db.execute(text("""
        SELECT id, question, answer, sources, steps, iterations, doc_ids,
               chart_data, matched_doc_ids, created_at
        FROM research_sessions WHERE id = :id AND parent_session_id IS NULL
    """), {"id": session_id}).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")
    children = db.execute(text("""
        SELECT question, answer, sources, chart_data, matched_doc_ids, created_at
        FROM research_sessions WHERE parent_session_id = :id ORDER BY created_at ASC
    """), {"id": session_id}).fetchall()
    thread = [{"question": c.question, "answer": c.answer or "", "sources": c.sources or [],
               "chartData": c.chart_data, "matchedDocIds": c.matched_doc_ids or []} for c in children]
    return {
        "id": str(row.id), "question": row.question, "answer": row.answer,
        "sources": row.sources or [], "steps": row.steps or [], "iterations": row.iterations,
        "doc_ids": row.doc_ids or [], "chart_data": row.chart_data,
        "matched_doc_ids": row.matched_doc_ids or [], "ts": row.created_at.isoformat(),
        "thread": thread,
    }


@router.delete("/history/{session_id}")
def delete_research_session(session_id: str, db: Session = Depends(get_db)):
    result = db.execute(
        text("DELETE FROM research_sessions WHERE id = :id RETURNING id"),
        {"id": session_id}
    ).fetchone()
    db.commit()
    if not result:
        raise HTTPException(404, "Session not found")
    return {"deleted": session_id}


class ActionConfirmRequest(BaseModel):
    approval_id: Optional[str] = None
    intent: str
    entities: dict


class ActionCancelRequest(BaseModel):
    approval_id: Optional[str] = None


@router.post("/action/confirm")
def research_action_confirm(body: ActionConfirmRequest):
    """User approved a HITL-gated action — execute it now."""
    if body.approval_id:
        try:
            import urllib.request as _ureq, json as _json, os as _os
            base = _os.environ.get("BACKEND_URL", "http://localhost:8000")
            data = _json.dumps({"decision": "approved", "comment": "Confirmed by user in Research Agent"}).encode()
            req  = _ureq.Request(f"{base}/api/approvals/{body.approval_id}/decide",
                                 data=data, headers={"Content-Type": "application/json"}, method="POST")
            _ureq.urlopen(req, timeout=10)
        except Exception as e:
            logger.warning(f"Could not record approval decision: {e}")
    try:
        result = _do_execute_action(body.intent, body.entities)
        return result
    except Exception as e:
        logger.error(f"Action confirm failed: {e}", exc_info=True)
        raise HTTPException(500, f"Action failed: {str(e)}")


@router.post("/action/cancel")
def research_action_cancel(body: ActionCancelRequest):
    """User rejected a HITL-gated action."""
    if body.approval_id:
        try:
            import urllib.request as _ureq, json as _json, os as _os
            base = _os.environ.get("BACKEND_URL", "http://localhost:8000")
            data = _json.dumps({"decision": "rejected", "comment": "Cancelled by user in Research Agent"}).encode()
            req  = _ureq.Request(f"{base}/api/approvals/{body.approval_id}/decide",
                                 data=data, headers={"Content-Type": "application/json"}, method="POST")
            _ureq.urlopen(req, timeout=10)
        except Exception as e:
            logger.warning(f"Could not record rejection: {e}")
    return {"success": True, "cancelled": True, "message": "Action cancelled."}


@router.get("/docs")
def list_research_docs(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT d.id, d.filename, d.doc_type, d.created_at,
               COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN document_chunks c ON c.document_id = d.id AND c.embedding IS NOT NULL
        WHERE d.status = 'completed'
        GROUP BY d.id, d.filename, d.doc_type, d.created_at
        ORDER BY d.created_at DESC
        LIMIT 100
    """)).fetchall()
    return [
        {
            "id": str(r.id),
            "filename": r.filename,
            "doc_type": r.doc_type or "unknown",
            "chunk_count": r.chunk_count or 0,
            "searchable": (r.chunk_count or 0) > 0,
        }
        for r in rows
    ]
