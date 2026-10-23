"""
Follow-up rewriting: "what about 2024?" + the previous turn → "What were
Lexicon's total revenues in 2024?". The standalone question then runs through
the normal pipeline (routing, structured answers, hybrid search), so a topic
change ("now show my passport") still works.
"""
import logging
import re
from typing import Dict, List, Optional

import ollama
from app.core import llm

from app.core import config as app_config
from app.core.config import NUM_CTX

logger = logging.getLogger(__name__)

_PROMPT = """Rewrite the user's latest message as ONE standalone question that makes sense without the conversation.
- Carry over the subject, document type (pay stubs, bank statement, passport, annual report...), person, company, dates and field from earlier turns when the latest message depends on them.
- If the latest message is already standalone or changes topic, return it unchanged.
- Do not answer it. Output only the rewritten question.

Example
Conversation:
Q: What were Lexicon's total revenues in 2025?
A: Total revenues were $49,803 thousand in 2025.
Latest: and in 2024?
Standalone: What were Lexicon's total revenues in 2024?

Example
Conversation:
Q: How many Zelle payments did I make in December 2025?
A: 2 payments totaling $51.00.
Latest: who were they sent to?
Standalone: Who were the Zelle payments I made in December 2025 sent to?

Example
Conversation:
Q: read the pay stubs and let me know 401k amount from Dec 2025 to Feb 2026
A: Based on 9 pay stubs: 401(k) $2,164.68.
Latest: what about the taxes for the same period?
Standalone: What were the taxes on my pay stubs from Dec 2025 to Feb 2026?

Example
Conversation:
Q: When does Nihal's passport expire?
A: 20/03/2027.
Latest: what is my property tax due?
Standalone: what is my property tax due?

Conversation:
{history}
Latest: {question}
Standalone:"""

# Messages that clearly lean on earlier turns
_DEPENDENT = re.compile(
    r"\b(it|its|they|them|their|that|those|these|this|he|she|his|her|same|also|too|again|more|"
    r"else|instead|above|previous|earlier|last one|what about|how about|and for|and in|why|explain)\b"
    r"|^\s*(and|or|but|so|what about|how about|then)\b", re.I)


_LEADS_ON = re.compile(r"^\s*(and|or|but|so|what about|how about|then|also)\b", re.I)


def needs_rewrite(question: str) -> bool:
    """Short or referring messages lean on earlier turns. A full question (15+ words) that
    doesn't open with "and / what about…" names its own subject and is used as typed."""
    q = question.strip()
    words = len(q.split())
    if words >= 15:
        return bool(_LEADS_ON.search(q))
    return words <= 6 or bool(_DEPENDENT.search(q))


def rewrite_followup(question: str, prior_qa: Optional[List[Dict]], always: bool = False) -> str:
    # `always` is kept for callers; a complete question is never rewritten (it costs a model call
    # and a rewrite can only lose detail)
    if not prior_qa or not needs_rewrite(question):
        return question
    def _flat(v) -> str:
        return " ".join(str(v or "").split())

    history = "\n".join(f"Q: {_flat(t.get('q'))[:300]}\nA: {_flat(t.get('a'))[:300]}" for t in prior_qa[-3:])
    try:
        resp = llm.chat(role="planner", 
            model=app_config.model,
            messages=[{"role": "user", "content": _PROMPT.format(history=history, question=question.strip())}],
            options={"num_ctx": NUM_CTX, "temperature": 0.0, "num_predict": 220},
        )
        out = resp["message"]["content"].strip().splitlines()[0].strip().strip('"')
        out = re.sub(r"^(standalone( question)?:)\s*", "", out, flags=re.I)
        # Keep just the question: models append citations or notes copied from answers.
        if question.count("?") <= 1:                   # several questions asked → keep them all
            out = re.split(r"(?<=\?)\s", out, maxsplit=1)[0]
        out = re.sub(r"\s*\[[^\]]*\]", "", out)
        out = re.sub(r"\s*\([^)]*\)", "", out)          # (notes / copied scope text)
        out = re.sub(r"\s*\([^)]*$", "", out).strip()     # unclosed "(" left by truncation
        if out and not out.endswith("?") and question.strip().endswith("?"):
            out += "?"
    except Exception as e:
        logger.warning(f"Follow-up rewrite failed ({e}) — using the message as-is")
        return question
    if not out or len(out.split()) < 2 or len(out) > max(400, len(question) * 2):
        return question
    if len(question.split()) >= 10 and len(out.split()) < 0.6 * len(question.split()):
        logger.info(f"🔁 Follow-up rewrite dropped too much — using the message as typed")
        return question
    logger.info(f"🔁 Follow-up rewritten: {question!r} → {out!r}")
    return out


def conversation_scope(question: str, context_sources: List[str], tenant_id: str) -> List[str]:
    """
    Document ids a follow-up should stay on: the files earlier turns answered
    from — unless the follow-up names a document type those files aren't
    ("…my passport" after a tax-report conversation), which is a topic change.
    """
    if not context_sources:
        return []
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.services.document_react_agent import _doctype_from_query
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id::text AS id, doc_type FROM documents WHERE tenant_id = :t AND status = 'completed' "
            "AND filename = ANY(:names)"), {"t": tenant_id, "names": list(context_sources)}).fetchall()
    finally:
        db.close()
    if not rows:
        return []
    named = _doctype_from_query(question)
    if named and named not in {r.doc_type for r in rows}:
        logger.info(f"Follow-up names '{named}' — new topic, not limiting to conversation documents")
        return []
    return [r.id for r in rows]
