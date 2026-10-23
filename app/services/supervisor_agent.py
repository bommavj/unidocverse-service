"""
Supervisor + Subagent orchestration for UniDocVerse.

Architecture:
    User Question
         │
    SupervisorAgent          ← classifies question into domain(s)
         │
    ┌────┴────────────┐
    │                 │      ← parallel if multi-domain
  BankingAgent   InsuranceAgent  PayrollAgent  LegalAgent  MedicalAgent  GeneralAgent
         │                 │
    run_research_agent (existing ReAct loop, domain-scoped)
         │
    Merger                  ← combines answers when multi-domain
         │
    Final Answer

Each subagent is a thin wrapper that calls run_research_agent with:
  - domain-specific doc_type_hint injected into the question context
  - domain-specific tool hints
  - only doc_ids that belong to its domain (when available)

The supervisor uses a fast keyword classifier first; falls back to LLM if ambiguous.
"""
import logging
import re

from app.utils.text_match import has_term, any_term
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Literal, Optional, Set, Tuple

import ollama
from app.core import llm

from app.core import config as app_config
from app.core.database import SessionLocal
from app.core.tenant import get_current_tenant_id

logger = logging.getLogger(__name__)


# ── Domain definitions ────────────────────────────────────────────────────────

DOMAINS = {
    "banking": {
        "label":      "Banking & Transactions",
        "doc_types":  ["bank_statement", "credit_card_statement", "transaction_log"],
        "keywords":   ["transaction", "spend", "merchant", "charge", "deposit",
                       "withdrawal", "balance", "bank", "credit card", "bank statement",
                       "atm", "transfer", "payment", "debit"],
        "hint":       "You are a banking specialist. Focus on transactions, spending patterns, balances.",
    },
    "insurance": {
        "label":      "Insurance",
        "doc_types":  ["insurance_policy", "declarations_page", "insurance_claim",
                       "insurance", "insurance_certificate"],
        "keywords":   ["policy", "premium", "coverage", "claim", "deductible",
                       "insurance", "beneficiary", "renewal", "liability", "eob"],
        "hint":       "You are an insurance specialist. Focus on policy terms, premiums, claims, coverage.",
    },
    "payroll": {
        "label":      "Payroll & HR",
        "doc_types":  ["pay_stub", "payroll_report", "w2", "hr_document",
                       "employee_roster"],
        "keywords":   ["salary", "payroll", "deduction", "gross", "net pay",
                       "w-2", "w2", "earnings", "tax withholding", "benefits",
                       "401k", "fica", "pay stub", "compensation"],
        "hint":       "You are a payroll specialist. Focus on earnings, deductions, tax withholding.",
    },
    "legal": {
        "label":      "Legal & Contracts",
        "doc_types":  ["contract", "legal", "agreement", "lease", "nda",
                       "clm_contract"],
        "keywords":   ["contract", "clause", "agreement", "obligation", "terms",
                       "liability", "indemnification", "termination", "legal",
                       "governing law", "jurisdiction"],
        "hint":       "You are a legal specialist. Focus on contract terms, obligations, risk clauses.",
    },
    "medical": {
        "label":      "Medical & Health",
        "doc_types":  ["medical_record", "medical", "eob", "lab_report"],
        "keywords":   ["diagnosis", "prescription", "doctor", "hospital", "medical",
                       "health", "patient", "treatment", "lab", "eob",
                       "insurance claim", "copay", "procedure"],
        "hint":       "You are a medical records specialist. Focus on diagnoses, treatments, billing.",
    },
    "financial": {
        "label":      "Financial Reports",
        "doc_types":  ["financial_statement", "annual_report", "quarterly_report",
                       "invoice", "expense_report", "purchase_order"],
        "keywords":   ["revenue", "profit", "loss", "income", "balance sheet",
                       "cash flow", "ebitda", "invoice", "expense", "budget",
                       "quarterly", "annual report", "fiscal"],
        "hint":       "You are a financial analyst. Focus on revenue, expenses, profitability metrics.",
    },
    "medical": {
        "label":      "Healthcare & Medical",
        "doc_types":  ["medical_record", "medical", "eob", "lab_report",
                       "prescription", "radiology_report"],
        "keywords":   ["diagnosis", "prescription", "doctor", "hospital", "medical",
                       "health", "patient", "treatment", "lab", "eob",
                       "copay", "procedure", "radiology", "clinical"],
        "hint":       "You are a medical records specialist. Focus on diagnoses, treatments, billing.",
    },
    "real_estate": {
        "label":      "Real Estate & Mortgages",
        "doc_types":  ["real_estate", "mortgage_document", "mortgage_statement",
                       "closing_disclosure", "loan_document", "lease"],
        "keywords":   ["mortgage", "property", "appraisal", "closing", "title deed",
                       "loan balance", "escrow", "real estate", "lease", "landlord",
                       "tenant", "rent", "refinance", "equity"],
        "hint":       "You are a real estate specialist. Focus on property values, mortgage terms, leases.",
    },
    "supply_chain": {
        "label":      "Supply Chain & Logistics",
        "doc_types":  ["purchase_order", "invoice", "bill_of_lading",
                       "customs_manifest", "inventory_report"],
        "keywords":   ["purchase order", "invoice", "vendor", "shipment", "customs",
                       "freight", "logistics", "inventory", "delivery", "supplier",
                       "bill of lading", "warehouse", "procurement"],
        "hint":       "You are a supply chain specialist. Focus on POs, invoices, shipments, vendors.",
    },
    "compliance": {
        "label":      "Compliance & Risk",
        "doc_types":  ["audit_report", "risk_assessment", "compliance_report",
                       "regulatory_filing", "soc_report"],
        "keywords":   ["compliance", "audit", "risk", "regulatory", "sox", "gdpr",
                       "hipaa", "finding", "control", "remediation", "violation",
                       "soc report", "deadline", "penalty"],
        "hint":       "You are a compliance specialist. Focus on audit findings, risk ratings, deadlines.",
    },
    "operations": {
        "label":      "Operations",
        "doc_types":  ["contract", "legal", "insurance_claim", "quote",
                       "approval_document", "case_document"],
        "keywords":   ["matter", "claim", "approval", "quote", "clm", "pipeline",
                       "jurisdiction", "case", "workflow", "bind", "renewal",
                       "approval queue", "pending"],
        "hint":       "You are an operations specialist. Focus on matters, claims, quotes, approvals.",
    },
    "general": {
        "label":      "General",
        "doc_types":  [],                       # catch-all
        "keywords":   [],
        "hint":       "You are a document analyst. Answer based on the available documents.",
    },
}


# ── Feature flag → domain mapping ────────────────────────────────────────────
# Each domain requires a feature flag to be ON in settings.
# "general" is always enabled (no flag needed — it's the catch-all).

DOMAIN_FLAG_MAP: Dict[str, str] = {
    "banking":      "finance_mode",        # Finance, Tax & Auditing
    "insurance":    "agency_mode",         # Insurance Agency Management
    "payroll":      "hr_mode",             # Human Resources (HR)
    "legal":        "legal_mode",          # Legal & Compliance
    "medical":      "medical_mode",        # Healthcare & Medical Admin
    "real_estate":  "real_estate_mode",    # Real Estate & Mortgages
    "supply_chain": "supply_chain_mode",   # Supply Chain & Logistics
    "compliance":   "compliance_mode",     # Compliance & Risk
    "operations":   "operations_mode",     # Operations
    "financial":    "finance_mode",        # Financial Reports (shares finance flag)
    "general":      None,                  # always enabled
}


def get_enabled_domains() -> Set[str]:
    """
    Read the settings table and return the set of domain keys whose
    feature flag is currently enabled. Always includes 'general'.
    """
    enabled = {"general"}
    try:
        from app.api.features_router import flags_for      # same per-organisation switches as the menu
        db = SessionLocal()
        try:
            flag_values = flags_for(db, str(get_current_tenant_id()))
        finally:
            db.close()
        for domain, flag in DOMAIN_FLAG_MAP.items():
            if flag is None or flag_values.get(flag):
                enabled.add(domain)
    except Exception as e:
        logger.warning(f"[Supervisor] Could not read feature flags, enabling all domains: {e}")
        enabled = set(DOMAINS.keys())
    return enabled


# ── Fast keyword classifier ───────────────────────────────────────────────────

def _classify_fast(question: str) -> List[str]:
    """
    Keyword-based domain classification. Returns list of matching domains
    (multi-domain when question spans e.g. payroll + insurance).
    Falls back to ['general'] when nothing matches.
    """
    q = question.lower()
    scores: Dict[str, int] = {d: 0 for d in DOMAINS if d != "general"}
    for domain, cfg in DOMAINS.items():
        if domain == "general":
            continue
        for kw in cfg["keywords"]:
            if has_term(q, kw):
                scores[domain] += 1

    # Return all domains with at least one keyword hit, sorted by score
    matched = sorted(
        [d for d, s in scores.items() if s > 0],
        key=lambda d: scores[d],
        reverse=True,
    )
    return matched or ["general"]


def _classify_llm(question: str, available_doc_types: List[str]) -> List[str]:
    """
    LLM-based classification when keywords are ambiguous.
    Returns list of domain keys (e.g. ['banking', 'payroll']).
    """
    domain_list = "\n".join(
        f"- {k}: {v['label']} — keywords: {', '.join(v['keywords'][:5])}"
        for k, v in DOMAINS.items()
        if k != "general"
    )
    doc_ctx = f"Available document types: {', '.join(available_doc_types)}" if available_doc_types else ""

    prompt = f"""Classify this user question into one or more domains. Return ONLY a JSON array of domain keys.

Domains:
{domain_list}

{doc_ctx}

Question: "{question}"

Rules:
- Return ["general"] if no specific domain fits
- Return multiple domains if the question spans them (e.g. comparing salary vs insurance)
- Return at most 2 domains
- Return only the JSON array, nothing else

Example: ["banking"] or ["payroll", "insurance"]"""

    try:
        resp = llm.chat(role="planner", 
            model=app_config.model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_predict": 50, "temperature": 0.0},
        )
        raw = resp["message"]["content"].strip()
        m = re.search(r'\[.*?\]', raw, re.DOTALL)
        if m:
            domains = [d.strip().strip('"').strip("'") for d in m.group().strip("[]").split(",")]
            valid = [d for d in domains if d in DOMAINS]
            if valid:
                return valid
    except Exception as e:
        logger.warning(f"Supervisor LLM classify failed: {e}")

    return _classify_fast(question)


# Short natural descriptions the zero-shot model reads. Keyword-list
# descriptions biased it toward whichever label had the broadest words.
# "general" lets it say "none of these" (e.g. passports have no specialist).
_DOMAIN_ZS_LABELS: Dict[str, str] = {
    "banking":      "bank account, card transactions or spending",
    "insurance":    "insurance policy, coverage or claim",
    "payroll":      "pay stub, salary or payroll",
    "legal":        "contract, lease or legal matter",
    "medical":      "doctor visit, prescription or medical record",
    "financial":    "company financial statements, profit and loss or balance sheet",
    "real_estate":  "mortgage, home loan or property",
    "supply_chain": "purchase order, shipping or vendor invoice",
    "compliance":   "audit, regulation or compliance risk",
    "operations":   "work order, maintenance or operations report",
    "general":      "hello, thanks, small talk or personal ID documents",
}


def _classify_zero_shot(question: str, enabled: Set[str]) -> List[str]:
    from app.services.classifiers import zero_shot_router as zsr

    labels = {d: desc for d, desc in _DOMAIN_ZS_LABELS.items() if d == "general" or d in enabled}
    ranked = zsr.score(question, labels, decision="domain")
    single = zsr.pick(ranked)
    if single:
        return [single]
    multi = [d for d in zsr.pick_multi(ranked) if d != "general"]
    if multi:
        logger.info(f"[Supervisor] zero-shot multi-domain: {multi}")
    return multi


def classify_question(
    question: str,
    available_doc_types: Optional[List[str]] = None,
    use_llm: bool = False,
) -> List[str]:
    """
    Classify a question into one or more domain keys.
    Only returns domains whose feature flag is currently enabled.
    Falls back to 'general' when the matched domain is disabled.
    """
    enabled = get_enabled_domains()

    fast = _classify_fast(question)
    # Filter to enabled domains only
    fast_enabled = [d for d in fast if d in enabled]

    if len(fast_enabled) == 1 and fast_enabled[0] != "general":
        return fast_enabled

    # Keywords found nothing → let the zero-shot model decide.
    # (Multi-domain keyword hits are kept as-is; they are usually right.)
    # Zero-shot is unreliable on 1-2 word inputs ("hello", "thanks")
    if (not fast_enabled or fast_enabled == ["general"]) and len(question.split()) >= 3:
        zs = _classify_zero_shot(question, enabled)
        if zs:
            return zs

    if not use_llm:
        return fast_enabled or ["general"]

    llm_domains = _classify_llm(question, available_doc_types or [])
    llm_enabled = [d for d in llm_domains if d in enabled]
    return llm_enabled or ["general"]


# ── Intent classification ─────────────────────────────────────────────────────

# Words that signal aggregation / analytical work — needs LangGraph + domain agents
_ANALYTICAL_SIGNALS = {
    "compare", "comparison", "total", "sum", "how much", "how many",
    "top", "bottom", "most", "least", "highest", "lowest",
    "all", "every", "across", "overall", "breakdown", "distribution",
    "trend", "over time", "month", "quarter", "year", "ytd", "mtd",
    "spending", "expenses", "spent", "average", "avg", "median",
    "list all", "show all", "find all", "summarize all",
    "analyze", "analysis", "report", "dashboard",
    "merchant", "vendor", "category", "group by",
    "risk", "findings", "audit", "compliance",
    "pipeline", "status across", "all claims", "all matters",
    "gap", "gaps", "missing coverage", "coverage gap",
    "auto and home", "home and auto", "insurance details",
    "policy details", "compare policies", "compare insurance",
}

# Words that signal simple lookup — fast path is enough
_SIMPLE_SIGNALS = {
    "what is", "what's", "who is", "when did", "when was", "where is",
    "define", "explain", "tell me about", "what does",
    "what are my", "show me my", "what's my",
    "read the", "read my", "let me know", "tell me the", "give me the details of",
    "thanks", "thank you", "ok", "okay", "got it", "understood",
    "yes", "no", "hello", "hi",
}


@dataclass
class IntentResult:
    route: Literal["fast", "langgraph", "clarify"]  # clarify = ask user for more detail
    complexity: Literal["simple", "analytical", "vague"]
    reason: str


# Any of these makes the question domain-anchored (not vague)
_DOMAIN_ANCHOR_KWS = {
    # banking / finance
    "bank", "transaction", "merchant", "statement", "spending", "expense",
    "balance", "account", "charge", "debit", "credit card",
    # insurance
    "insurance", "policy", "claim", "premium", "coverage", "deductible",
    "co-pay", "copay", "liability", "endorsement",
    # payroll / HR
    "payroll", "pay stub", "salary", "w-2", "w2", "ytd", "gross pay",
    "net pay", "deduction", "withholding", "401k", "fica",
    # legal
    "contract", "matter", "clause", "agreement", "legal", "lawsuit",
    # medical
    "medical", "diagnosis", "prescription", "eob", "health",
    # real estate
    "mortgage", "deed", "lease", "property", "closing",
    # supply chain / compliance
    "invoice", "purchase order", "vendor", "audit", "compliance",
    # personal identity documents
    "passport", "birth certificate", "visa", "id card", "driver", "license",
    "social security", "immigration", "green card", "uscis", "i-94",
    "vaccination", "vaccine", "marriage certificate", "death certificate",
    # general document reads (user explicitly names a document type)
    "receipt", "utility bill", "tax", "w2", "1099", "rent", "hoa",
}


def classify_intent(question: str, doc_ids: List[str] = None) -> IntentResult:
    """
    Decide whether a question needs the full LangGraph workflow, fast path,
    or a clarification request (when the question is too vague to answer well).

    Fast path:   short/conversational questions, simple lookups, single-doc context
    LangGraph:   aggregation, cross-domain, trends, SQL-heavy, multi-doc synthesis
    Clarify:     no domain anchor + vague retrieval phrase → ask user to be specific

    Never calls the LLM — pure keyword heuristic, <1ms.
    """
    q = question.lower().strip()
    words = q.split()
    word_count = len(words)

    # 0. Vague check — only fire when the query has NO content at all (≤3 words, no nouns).
    #    "details", "information", "show me" appear in nearly every natural query, so
    #    broad vague-pattern matching kills legitimate queries. Only clarify when the
    #    entire query is a bare retrieval phrase with zero subject matter.
    _BARE_VAGUE = {"documents", "files", "stuff", "things", "data", "information", "details"}
    if not doc_ids and word_count <= 3 and all(w in _BARE_VAGUE or len(w) <= 3 for w in words):
        return IntentResult("clarify", "vague", "bare retrieval phrase with no subject")

    # 1. Analytical signals always win — check before any word-count shortcuts
    for sig in _ANALYTICAL_SIGNALS:
        if has_term(q, sig):
            return IntentResult("langgraph", "analytical", f"analytical signal: '{sig}'")

    # 2. Very short or conversational → fast
    if word_count <= 4:
        return IntentResult("fast", "simple", "very short question")

    # 3. Explicit simple-signal phrase at the start → fast
    for sig in _SIMPLE_SIGNALS:
        if q.startswith(sig) or q == sig.rstrip("?"):
            return IntentResult("fast", "simple", f"simple signal: '{sig}'")

    # 4. Multi-domain keyword hit → LangGraph for specialist routing
    fast_domains = _classify_fast(question)
    if len(fast_domains) > 1:
        return IntentResult("langgraph", "analytical", f"multi-domain: {fast_domains}")

    # 5. Single scoped doc, no aggregation → fast path (doc Q&A is already handled
    #    by ask_router, but if it lands here, keep it fast).
    #    Extend to word_count <= 25: "give me page by page information of this document"
    #    is clearly a doc-scoped question even at 11 words.
    if doc_ids and len(doc_ids) == 1 and word_count <= 25:
        return IntentResult("fast", "simple", "single-doc question")

    # 6. Domain-specific but short → fast
    if word_count <= 10:
        return IntentResult("fast", "simple", f"short domain question ({word_count} words)")

    # 7. Default: long question with no analytical signals — let LangGraph handle
    return IntentResult("langgraph", "analytical", "long question, defaulting to full workflow")


# ── Agent registry ────────────────────────────────────────────────────────────
# Maps domain key → agent class. Add new domains here.

def _get_agent_class(domain: str):
    from app.services.agents import (
        BankingAgent, InsuranceAgent, PayrollAgent, LegalAgent,
        MedicalAgent, RealEstateAgent, SupplyChainAgent,
        ComplianceAgent, OperationsAgent,
    )
    registry = {
        "banking":      BankingAgent,
        "insurance":    InsuranceAgent,
        "payroll":      PayrollAgent,
        "legal":        LegalAgent,
        "medical":      MedicalAgent,
        "real_estate":  RealEstateAgent,
        "supply_chain": SupplyChainAgent,
        "compliance":   ComplianceAgent,
        "operations":   OperationsAgent,
    }
    return registry.get(domain)


# ── Subagent runner ───────────────────────────────────────────────────────────

def _run_subagent(
    domain: str,
    question: str,
    doc_ids: List[str],
    stream_answer: bool = False,
    page: int = 0,
    prior_qa: Optional[List] = None,
) -> Dict:
    """
    Run the domain-specific agent class if one exists,
    otherwise fall back to run_research_agent with domain hint.
    """
    cfg = DOMAINS.get(domain, DOMAINS["general"])
    t0 = time.time()

    agent_class = _get_agent_class(domain)
    if agent_class:
        # Use the specialized domain agent with its own tools and prompt
        agent = agent_class()
        try:
            result_obj = agent.run(question=question, doc_ids=doc_ids)
            result = result_obj.to_dict()
            result["question"] = question
        finally:
            del agent
    else:
        # Fall back to the existing ReAct agent with domain hint injected
        from app.services.document_react_agent import run_research_agent
        scoped_question = f"[Domain: {cfg['label']}] {question}"
        result = run_research_agent(
            question=scoped_question,
            doc_ids=doc_ids,
            page=page,
            stream_answer=stream_answer,
            prior_qa=prior_qa,
        )
        result["question"] = question

    result["_domain"] = domain
    result["_domain_label"] = cfg["label"]
    result["_latency_ms"] = int((time.time() - t0) * 1000)
    return result


# ── Multi-domain merger ───────────────────────────────────────────────────────

def _merge_results(results: List[Dict], question: str) -> Dict:
    """
    Merge answers from multiple subagents into a single coherent response.
    If only one result, pass through unchanged.
    """
    if len(results) == 1:
        return results[0]

    # Build a combined answer with section headers per domain
    sections = []
    all_sources = []
    all_steps = []
    chart_data = None

    for r in results:
        domain_label = r.get("_domain_label", "General")
        answer = (r.get("answer") or "").strip()
        if answer:
            sections.append(f"### {domain_label}\n{answer}")
        all_sources.extend(r.get("sources") or [])
        all_steps.extend(r.get("steps") or [])
        # Use chart from first result that has one
        if not chart_data and r.get("chart_data"):
            chart_data = r["chart_data"]

    combined_answer = "\n\n".join(sections) if sections else "No results found."
    unique_sources = list(dict.fromkeys(all_sources))  # deduplicate preserving order

    return {
        "question":    question,
        "answer":      combined_answer,
        "sources":     unique_sources,
        "steps":       all_steps,
        "iterations":  sum(r.get("iterations", 0) for r in results),
        "chart_data":  chart_data,
        "_multi_domain": True,
        "_domains":    [r.get("_domain") for r in results],
    }


# ── Public entry point ────────────────────────────────────────────────────────

def run_supervisor(
    question: str,
    doc_ids: Optional[List[str]] = None,
    page: int = 0,
    stream_answer: bool = False,
    prior_qa: Optional[List] = None,
    available_doc_types: Optional[List[str]] = None,
    use_llm_routing: bool = False,
    max_parallel: int = 2,
) -> Dict:
    """
    Main entry point — replaces run_research_agent with supervisor routing.

    Flow:
      1. Classify question → domain(s)
      2. Run each domain's subagent (parallel if multi-domain)
      3. Merge results
      4. Return unified response

    Args:
        question:             User's question
        doc_ids:              Scoped document IDs (empty = all docs)
        page:                 Chunk pagination page
        stream_answer:        Whether subagents should run in streaming mode
        prior_qa:             Prior Q&A context for follow-ups
        available_doc_types:  Doc types in scope (helps classifier)
        use_llm_routing:      Use LLM for routing (slower, more accurate for ambiguous)
        max_parallel:         Max subagents to run in parallel
    """
    doc_ids = doc_ids or []

    # Step 1: classify
    domains = classify_question(question, available_doc_types, use_llm=use_llm_routing)
    domains = domains[:max_parallel]  # cap parallelism
    logger.info(f"[Supervisor] question={question!r} → domains={domains}")

    # Step 2: run subagents
    if len(domains) == 1:
        # Fast path — no threading overhead for single domain
        result = _run_subagent(
            domain=domains[0],
            question=question,
            doc_ids=doc_ids,
            stream_answer=stream_answer,
            page=page,
            prior_qa=prior_qa,
        )
        results = [result]
    else:
        # Parallel execution for multi-domain questions
        results = []
        with ThreadPoolExecutor(max_workers=len(domains)) as pool:
            futures = {
                pool.submit(
                    _run_subagent,
                    domain, question, doc_ids, stream_answer, page, prior_qa,
                ): domain
                for domain in domains
            }
            for future in as_completed(futures):
                domain = futures[future]
                try:
                    results.append(future.result())
                    logger.info(f"[Supervisor] subagent '{domain}' completed")
                except Exception as e:
                    logger.error(f"[Supervisor] subagent '{domain}' failed: {e}")

    # Step 3: merge
    return _merge_results(results, question)
