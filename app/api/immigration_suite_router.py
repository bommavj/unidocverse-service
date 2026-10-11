"""
Immigration suite API.

  /api/immigration-suite/…   team endpoints (JWT, tenant-scoped): cases, leads, intake, signatures, reports, AI
  /public/imm/…              client pages opened from a link (random token, no account): intake form, signing
"""
import html
import json
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_scope import current_tenant
from app.services.immigration import suite

router = APIRouter(prefix="/api/immigration-suite", tags=["immigration-suite"])
public = APIRouter(prefix="/public/imm", tags=["immigration-public"])


def _t() -> str:
    return str(current_tenant())


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except LookupError as e:
        raise HTTPException(404, f"{str(e).capitalize() or 'Record'} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


def _public_link(request: Request, path: str) -> dict:
    """
    Client links need an address the client's browser can reach. UDV_PUBLIC_URL (e.g. https://app.yourfirm.com,
    or a tunnel such as Cloudflare Tunnel exposing only /public/imm) wins; otherwise the address of this request.
    """
    import ipaddress
    import os
    from urllib.parse import urlparse
    base = (os.getenv("UDV_PUBLIC_URL") or str(request.base_url)).rstrip("/")
    host = urlparse(base).hostname or ""
    try:
        local = host in ("localhost", "") or host.endswith(".local") or ipaddress.ip_address(host).is_private \
            or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost" or host.endswith(".local")
    out = {"link": base + path, "reachable": not local}
    if local:
        out["warning"] = ("This link only works on this computer (or your local network). To let clients open it, set "
                          "UDV_PUBLIC_URL to your public address — a server domain or a secure tunnel to /public/imm — and "
                          "restart. Until then, use Open to fill it in with the client.")
    return out


def _actor(request: Request) -> Optional[str]:
    u = getattr(request.state, "user", None)
    return (u or {}).get("username") if isinstance(u, dict) else None


# ── Rules ─────────────────────────────────────────────────────────────────────

@router.get("/meta")
def meta():
    r = suite.rules()
    return {"statuses": r["statuses"], "lead_stages": suite.LEAD_STAGES, "languages": r["languages"],
            "case_types": {k: {"label": v["label"], "form": v["form"], "visa_class": v.get("visa_class"),
                               "evidence": v.get("evidence", []),
                               "sections": [{"title": r["intake_sections"][s]["title"],
                                             "questions": [f["label"] for f in r["intake_sections"][s]["fields"]]}
                                            for s in v.get("intake_sections", [])]}
                           for k, v in r["case_types"].items()}}


# ── Cases ─────────────────────────────────────────────────────────────────────

@router.get("/cases")
def cases(status: Optional[str] = None, q: Optional[str] = None, db: Session = Depends(get_db)):
    return suite.list_cases(db, _t(), status, q)


@router.post("/cases", status_code=201)
def create_case(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.create_case, db, _t(), body, _actor(request))


@router.get("/cases/suggestions")
def suggestions(db: Session = Depends(get_db)):
    return suite.suggest_cases(db, _t())


@router.get("/cases/{case_id}")
def get_case(case_id: str, db: Session = Depends(get_db)):
    return _guard(suite.get_case, db, _t(), case_id)


@router.patch("/cases/{case_id}")
def update_case(case_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.update_case, db, _t(), case_id, body, _actor(request))


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: str, db: Session = Depends(get_db)):
    _guard(suite.delete_case, db, _t(), case_id)


@router.post("/cases/{case_id}/notes", status_code=201)
def add_note(case_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    if not (body.get("text") or "").strip():
        raise HTTPException(400, "Note is empty")
    _guard(suite.add_note, db, _t(), case_id, body["text"], _actor(request))
    return {"ok": True}


@router.patch("/cases/{case_id}/checklist/{item_id}")
def check(case_id: str, item_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    _guard(suite.toggle_check, db, _t(), case_id, item_id, bool(body.get("done")))
    return {"ok": True}


@router.post("/cases/{case_id}/match-documents")
def match(case_id: str, db: Session = Depends(get_db)):
    return {"matched": _guard(suite.match_checklist, db, _t(), case_id)}


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.get("/cases/{case_id}/tasks")
def tasks(case_id: str, db: Session = Depends(get_db)):
    return _guard(suite.list_tasks, db, _t(), case_id)


@router.post("/cases/{case_id}/tasks", status_code=201)
def add_task(case_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.add_task, db, _t(), case_id, body, _actor(request))


@router.post("/cases/{case_id}/tasks/template", status_code=201)
def apply_template(case_id: str, request: Request, db: Session = Depends(get_db)):
    return _guard(suite.apply_task_template, db, _t(), case_id, _actor(request))


@router.patch("/cases/{case_id}/tasks/{task_id}")
def update_task(case_id: str, task_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.update_task, db, _t(), case_id, task_id, body, _actor(request))


@router.delete("/cases/{case_id}/tasks/{task_id}", status_code=204)
def delete_task(case_id: str, task_id: str, request: Request, db: Session = Depends(get_db)):
    _guard(suite.delete_task, db, _t(), case_id, task_id, _actor(request))


# ── CRM ───────────────────────────────────────────────────────────────────────

@router.get("/leads")
def leads(db: Session = Depends(get_db)):
    return suite.list_leads(db, _t())


@router.post("/leads", status_code=201)
def create_lead(body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.save_lead, db, _t(), body)


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.save_lead, db, _t(), body, lead_id)


@router.post("/leads/{lead_id}/convert", status_code=201)
def convert(lead_id: str, request: Request, body: dict = Body(default={}), db: Session = Depends(get_db)):
    return _guard(suite.convert_lead, db, _t(), lead_id, body.get("case_type"), _actor(request))


@router.delete("/leads/{lead_id}", status_code=204)
def delete_lead(lead_id: str, db: Session = Depends(get_db)):
    suite.delete_lead(db, _t(), lead_id)


# ── Intake ────────────────────────────────────────────────────────────────────

@router.post("/cases/{case_id}/intake", status_code=201)
def send_intake(case_id: str, request: Request, body: dict = Body(default={}), db: Session = Depends(get_db)):
    r = _guard(suite.create_intake, db, _t(), case_id, body.get("language", "en"))
    r.update(_public_link(request, f"/public/imm/intake/{r.pop('token')}"))
    return r


@router.get("/intakes")
def intakes(db: Session = Depends(get_db)):
    return suite.list_intakes(db, _t())


@router.get("/intakes/{intake_id}")
def intake(intake_id: str, db: Session = Depends(get_db)):
    return _guard(suite.intake_answers, db, _t(), intake_id)


@router.get("/intakes/{intake_id}/pdf")
def intake_pdf(intake_id: str, db: Session = Depends(get_db)):
    pdf = _guard(suite.intake_pdf, db, _t(), intake_id)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="intake_{intake_id[:8]}.pdf"'})


# ── E-signature ───────────────────────────────────────────────────────────────

@router.get("/signatures")
def signatures(db: Session = Depends(get_db)):
    return suite.list_signatures(db, _t())


@router.post("/cases/{case_id}/signatures", status_code=201)
def request_signature(case_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    r = _guard(suite.create_signature, db, _t(), case_id, body.get("title", ""), body.get("signer_name", ""),
               body.get("signer_email"), body.get("document_id"))
    r.update(_public_link(request, f"/public/imm/sign/{r.pop('token')}"))
    return r


@router.get("/signatures/{sig_id}/certificate")
def certificate(sig_id: str, db: Session = Depends(get_db)):
    pdf = _guard(suite.signature_certificate, db, _t(), sig_id)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="signed_{sig_id[:8]}.pdf"'})


@router.post("/signatures/{sig_id}/void")
def void(sig_id: str, db: Session = Depends(get_db)):
    suite.void_signature(db, _t(), sig_id)
    return {"ok": True}


# ── Reports, AI, translation ──────────────────────────────────────────────────

@router.get("/cases/{case_id}/packet/sources")
def packet_sources(case_id: str, db: Session = Depends(get_db)):
    return _guard(suite.packet_sources, db, _t(), case_id)


@router.post("/cases/{case_id}/packet")
def packet(case_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    pdf = _guard(suite.build_packet, db, _t(), case_id, body.get("items") or [], body.get("preparer"),
                 body.get("letter_text") or "", _actor(request))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="packet_{case_id[:8]}.pdf"'})


@router.get("/cases/{case_id}/forms")
def case_forms(case_id: str, db: Session = Depends(get_db)):
    return {"forms": _guard(suite.forms_for_case, db, _t(), case_id)}


@router.get("/cases/{case_id}/forms/{form}")
def form_preview(case_id: str, form: str, db: Session = Depends(get_db)):
    return _guard(suite.form_preview, db, _t(), case_id, form)


@router.patch("/cases/{case_id}/forms/{form}")
def form_save(case_id: str, form: str, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.save_form_data, db, _t(), case_id, form, body.get("values") or {})


@router.get("/cases/{case_id}/forms/{form}/pdf")
def form_pdf(case_id: str, form: str, request: Request, db: Session = Depends(get_db)):
    pdf = _guard(suite.form_pdf, db, _t(), case_id, form, _actor(request))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{form.upper()}_{case_id[:8]}.pdf"'})


@router.get("/report")
def report(db: Session = Depends(get_db)):
    return suite.report(db, _t())


@router.get("/report/export")
def report_export(format: str = "xlsx", db: Session = Depends(get_db)):
    from datetime import date as _d
    if format == "pdf":
        data, media = suite.report_pdf(db, _t()), "application/pdf"
    elif format == "xlsx":
        data, media = suite.report_xlsx(db, _t()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise HTTPException(400, "format must be xlsx or pdf")
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="immigration_report_{_d.today()}.{format}"'})


@router.post("/cases/{case_id}/draft")
def draft(case_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.draft, db, _t(), case_id, body.get("kind", ""), body.get("instructions", ""))


@router.post("/translate")
def translate(body: dict = Body(...)):
    return _guard(suite.translate, body.get("text", ""), body.get("language", ""))


# ── Public client pages ───────────────────────────────────────────────────────

_CSS = """
:root{--ink:#0f172a;--muted:#475569;--line:#e2e8f0;--accent:#4338ca;--bg:#f8fafc}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:32px 16px} h1{font-size:22px;margin:0 0 4px} .sub{color:var(--muted);margin:0 0 24px}
section{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:16px}
h2{font-size:16px;margin:0 0 12px} label{display:block;font-size:13px;font-weight:600;margin:12px 0 4px}
input,select,textarea{width:100%;padding:9px 10px;border:1px solid #cbd5e1;border-radius:6px;font:inherit}
textarea{min-height:70px} input:focus,select:focus,textarea:focus{outline:2px solid #a5b4fc;border-color:var(--accent)}
.req{color:#b91c1c} .row{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
button{padding:10px 18px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button.sec{background:#fff;color:var(--accent)} button:focus-visible{outline:2px solid #a5b4fc;outline-offset:2px}
.msg{padding:12px 14px;border-radius:8px;margin-bottom:16px} .ok{background:#ecfdf5;color:#065f46} .err{background:#fef2f2;color:#991b1b}
.note{font-size:12px;color:var(--muted)} .tr{display:block;font-weight:400;color:var(--muted);font-size:12px}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{html.escape(title)}</title><style>{_CSS}</style></head><body><main>{body}</main></body></html>""",
                        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


def _gone() -> HTMLResponse:
    r = _page("Link not available", "<h1>This link isn't available</h1><p class='sub'>It may have expired, been "
                                     "completed already, or been cancelled. Please contact your legal team.</p>")
    r.status_code = 404
    return r


@public.get("/intake/{token}", response_class=HTMLResponse)
def intake_page(token: str, db: Session = Depends(get_db)):
    it = suite.intake_by_token(db, token)
    if not it:
        return _gone()
    if it["status"] == "submitted":
        return _page("Submitted", "<h1>Thank you</h1><p class='sub'>Your answers were submitted. Your legal team will be in touch.</p>")
    schema = suite.intake_schema(it["case_type"])
    tr = []
    if it["language"] != "en":
        try:
            tr = suite.translated_labels(it["case_type"], it["language"]).splitlines()
        except Exception:
            tr = []
    n_labels = len(schema) + sum(len(s["fields"]) for s in schema)
    tr = [t.strip() for t in tr if t.strip()]
    tr = tr if len(tr) == n_labels else []                 # only use a clean line-for-line translation
    idx = iter(range(n_labels))
    answers = it.get("answers") or {}
    secs = []
    for s in schema:
        i = next(idx)
        head = html.escape(s["title"]) + (f"<span class='tr'>{html.escape(tr[i])}</span>" if tr else "")
        fields = []
        for f in s["fields"]:
            j = next(idx)
            lab = html.escape(f["label"]) + (" <span class='req'>*</span>" if f.get("required") else "") \
                + (f"<span class='tr'>{html.escape(tr[j])}</span>" if tr else "")
            val = html.escape(str(answers.get(f["key"], "")))
            name, typ = html.escape(f["key"]), f.get("type", "text")
            if typ == "textarea":
                ctl = f"<textarea id='{name}' name='{name}'>{val}</textarea>"
            elif typ == "select":
                opts = "".join(f"<option{' selected' if o == answers.get(f['key']) else ''}>{html.escape(o)}</option>"
                               for o in [""] + f.get("options", []))
                ctl = f"<select id='{name}' name='{name}'>{opts}</select>"
            else:
                t = {"date": "date", "number": "number", "email": "email"}.get(typ, "text")
                ctl = f"<input id='{name}' name='{name}' type='{t}' value='{val}' autocomplete='off'>"
            fields.append(f"<label for='{name}'>{lab}</label>{ctl}")
        secs.append(f"<section><h2>{head}</h2>{''.join(fields)}</section>")
    case_label = html.escape(suite.rules()["case_types"][it["case_type"]]["label"])
    body = f"""<h1>Immigration questionnaire</h1><p class="sub">{case_label} · for {html.escape(it['beneficiary'])}</p>
<div id="msg"></div><form id="f">{''.join(secs)}
<div class="row"><button type="submit">Submit answers</button><button type="button" class="sec" id="save">Save and finish later</button></div>
<p class="note">Your answers go only to your legal team. Fields marked * are required to submit.</p></form>
<script>
const f=document.getElementById('f'),msg=document.getElementById('msg');
async function send(final){{const a={{}};new FormData(f).forEach((v,k)=>a[k]=v);
 const r=await fetch(location.pathname,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{answers:a,final}})}});
 const j=await r.json().catch(()=>({{}}));
 msg.className='msg '+(r.ok?'ok':'err');msg.textContent=r.ok?(final?'Submitted — thank you.':'Saved. You can come back to this link later.'):(j.detail||'Something went wrong');
 if(r.ok&&final)f.remove();window.scrollTo(0,0);}}
f.addEventListener('submit',e=>{{e.preventDefault();send(true)}});document.getElementById('save').onclick=()=>send(false);
</script>"""
    return _page("Immigration questionnaire", body)


@public.post("/intake/{token}")
def intake_submit(token: str, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.submit_intake, db, token, body.get("answers") or {}, bool(body.get("final")))


def _client(request: Request):
    return (request.client.host if request.client else ""), request.headers.get("user-agent", "")


@public.get("/sign/{token}", response_class=HTMLResponse)
def sign_page(token: str, request: Request, db: Session = Depends(get_db)):
    s = suite.mark_viewed(db, token, *_client(request))
    if not s or s["status"] in ("declined", "voided"):
        return _gone()
    if s["status"] == "signed":
        return _page("Signed", f"<h1>Signed</h1><p class='sub'>“{html.escape(s['title'])}” was signed on "
                               f"{html.escape(str(s['signed_at'])[:16].replace('T', ' '))} UTC.</p>")
    doc = (f"<p><a href='/public/imm/sign/{html.escape(token)}/document' target='_blank' rel='noopener'>Open the document to review it</a>"
           f"</p><p class='note'>Document fingerprint (SHA-256): {html.escape(s['document_sha256'])}</p>") if s.get("document_id") else ""
    body = f"""<h1>Signature requested</h1><p class="sub">{html.escape(s['title'])}</p><div id="msg"></div>
<section>{doc}<form id="f">
<label for="name">Type your full name to sign: <b>{html.escape(s['signer_name'])}</b></label>
<input id="name" name="name" autocomplete="name" required>
<label style="font-weight:400"><input type="checkbox" id="consent" style="width:auto"> I agree to sign this document
electronically, and that my typed name is my signature.</label>
<div class="row"><button type="submit">Sign</button><button type="button" class="sec" id="decline">Decline</button></div></form></section>
<p class="note">The time of signing, your IP address and browser are recorded with your signature.</p>
<script>
const f=document.getElementById('f'),msg=document.getElementById('msg');
async function go(decline){{const r=await fetch(location.pathname,{{method:'POST',headers:{{'Content-Type':'application/json'}},
 body:JSON.stringify({{name:document.getElementById('name').value,consent:document.getElementById('consent').checked,decline}})}});
 const j=await r.json().catch(()=>({{}}));msg.className='msg '+(r.ok?'ok':'err');
 msg.textContent=r.ok?(decline?'You declined to sign.':'Signed — thank you.'):(j.detail||'Something went wrong');if(r.ok)f.remove();}}
f.addEventListener('submit',e=>{{e.preventDefault();go(false)}});document.getElementById('decline').onclick=()=>go(true);
</script>"""
    return _page("Signature requested", body)


@public.get("/sign/{token}/document")
def sign_document(token: str, db: Session = Depends(get_db)):
    from pathlib import Path
    from sqlalchemy import text
    s = suite.signature_by_token(db, token)
    if not s or not s.get("document_id") or s["status"] in ("declined", "voided"):
        raise HTTPException(404, "Not found")
    r = db.execute(text("SELECT filename, file_path FROM documents WHERE id::text = :i AND tenant_id = :t"),
                   {"i": s["document_id"], "t": str(s["tenant_id"])}).fetchone()
    if not r or not Path(r.file_path).exists():
        raise HTTPException(404, "Not found")
    media = "application/pdf" if r.filename.lower().endswith(".pdf") else "application/octet-stream"
    return Response(Path(r.file_path).read_bytes(), media_type=media,
                    headers={"Content-Disposition": f'inline; filename="{r.filename}"', "Cache-Control": "no-store"})


@public.post("/sign/{token}")
def sign_submit(token: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(suite.sign, db, token, body.get("name", ""), bool(body.get("consent")), *_client(request),
                  decline=bool(body.get("decline")))
