"""
LangGraph-based query workflow for UniDocVerse.

This is the SECOND StateGraph in the system alongside the ingestion pipeline
(app/agents/workflow.py). It handles the research/query side:

    User Question
         │
    [supervisor_node]         ← classify domains + build execution plan
         │
    Send() fan-out            ← parallel for independent domains
    ┌────┬────┬────┐
    │    │    │    │
  banking insurance payroll  … (one node per domain)
    │    │    │    │
    └────┴────┴────┘
    [merger_node]             ← collect AgentResults, resolve cross-domain deps
         │
    [synthesizer_node]        ← single LLM call → final polished answer
         │
         END

Design notes:
  - Supervisor uses a fast keyword classifier + optional LLM fallback (same as
    supervisor_agent.py) to decide which domains are relevant.
  - Domain agent nodes are pure executors: they call the existing agent classes
    (BankingAgent, InsuranceAgent, …) and return AgentResult.
  - Send() API is used for true parallel fan-out to independent domain nodes.
  - The merger node aggregates results; if a domain result references data from
    another domain's result, it re-runs the dependent agent with the enriched
    context (cross-domain DAG step).
  - Synthesizer does one final LLM call to produce a coherent answer.
  - All nodes respect tenant_id via ContextVar (set by TenantMiddleware).
"""

import logging
import os
import operator
import time
from typing import Any, Annotated, Dict, List, Optional, Sequence, TypedDict

import ollama
from app.core import llm
from langgraph.types import Send
from langgraph.graph import END, StateGraph

from app.core import config as app_config
from app.core.database import SessionLocal
from app.core.tenant import apply_tenant_to_db, get_current_tenant_id
from app.services.agents import (
    AgentResult,
    BankingAgent,
    ComplianceAgent,
    InsuranceAgent,
    LegalAgent,
    MedicalAgent,
    OperationsAgent,
    PayrollAgent,
    RealEstateAgent,
    SupplyChainAgent,
)
from app.services.supervisor_agent import (
    DOMAINS,
    classify_question,
    get_enabled_domains,
)

logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────


class QueryState(TypedDict):
    """Shared state flowing through the query LangGraph."""

    # Input
    question: str
    tenant_id: str
    doc_ids: List[str]

    # Supervisor output
    domains: List[str]                      # domains to run (ordered by priority)
    plan: Dict[str, Any]                    # {"parallel": [...], "sequential": [...]}

    # Domain agent results — reducer appends new items
    agent_results: Annotated[List[AgentResult], operator.add]

    # Final output
    answer: str
    sources: List[Dict[str, Any]]
    chart_data: Optional[Dict[str, Any]]

    # Internal
    errors: Annotated[List[str], operator.add]
    started_at: float


# ── Agent class registry ──────────────────────────────────────────────────────

_USE_DOMAIN_AGENTS = os.getenv("USE_DOMAIN_AGENTS", "0") == "1"

_AGENT_CLASSES = {
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


# ── Node functions ─────────────────────────────────────────────────────────────

def supervisor_node(state: QueryState) -> Dict[str, Any]:
    """
    Classify the question into domains, build an execution plan.

    Returns state updates:
      - domains: list of matched domain keys
      - plan: {"parallel": [domain, ...], "sequential": [[dep_domain, domain], ...]}
    """
    question = state["question"]
    tenant_id = state.get("tenant_id") or get_current_tenant_id()

    logger.info(f"[supervisor_node] Classifying: {question[:80]}")

    # Use existing classifier (keyword first, LLM fallback)
    matched = classify_question(question)  # returns List[str] of domain keys

    # Filter to enabled domains for this tenant
    enabled = get_enabled_domains()
    # Only domains with a graph node — the classifier's domain list is broader
    # (e.g. "financial" has no agent) and an unknown Send target crashes the graph.
    domains = [d for d in matched if d in enabled and d in _AGENT_CLASSES]
    if not _USE_DOMAIN_AGENTS or not domains:
        # One research agent answers everything: it has the structured paths
        # (transactions, pay stubs, tables, tax rules/audit) that the per-domain
        # agents bypassed (e.g. "how many Zelle payments" got a top-merchants list).
        domains = ["general"]

    # Build execution plan:
    # - Most domains are independent → run in parallel
    # - Exception: operations depends on insurance (claims data) and legal (matters)
    #   when both are requested together — run ops after insurance+legal finish.
    sequential_pairs: List[List[str]] = []
    parallel_domains: List[str] = list(domains)

    if "operations" in domains and ("insurance" in domains or "legal" in domains):
        # operations can enrich its answer with insurance claim counts and legal matter IDs
        # move ops to sequential after insurance/legal
        parallel_domains = [d for d in domains if d != "operations"]
        sequential_pairs.append(["operations"])   # runs after merger receives insurance+legal

    plan = {
        "parallel":   parallel_domains,
        "sequential": sequential_pairs,
    }

    logger.info(f"[supervisor_node] plan={plan}")
    return {
        "domains":    domains,
        "plan":       plan,
        "tenant_id":  tenant_id,
        "started_at": time.time(),
    }


def _run_domain_agent(domain: str, question: str, doc_ids: List[str], tenant_id: str) -> AgentResult:
    """
    Instantiate the domain agent class and run it.
    Falls back to a lightweight vector-search answer if the class is missing.
    """
    agent_cls = _AGENT_CLASSES.get(domain)

    # Exact answers from structured data beat any agent's LLM reasoning.
    from app.services.structured_answer import try_structured_answer
    structured = try_structured_answer(question, tenant_id, doc_ids)
    if structured:
        return AgentResult(
            answer=structured["answer"],
            sources=structured["sources"],
            steps=[{"tool": structured["tool"], "thought": "Computed from structured data",
                    "args": structured["detail"], "observation": {"sources": structured["sources"]}}],
            chart_data=None,
            domain=domain,
        )

    db = SessionLocal()
    try:
        apply_tenant_to_db(db, tenant_id)
        if agent_cls:
            agent = agent_cls(db=db)
            result = agent.run(question=question, doc_ids=doc_ids)
        else:
            # general domain: run a simple search-based answer
            from app.services.document_react_agent import run_research_agent
            raw = run_research_agent(
                question=question,
                doc_ids=doc_ids,
            )
            result = AgentResult(
                answer=raw.get("answer", ""),
                sources=raw.get("sources", []),
                steps=raw.get("steps", []),
                chart_data=raw.get("chart_data"),
                domain="general",
            )
    finally:
        db.close()
    return result


from app.core.llm import CloudConsentRequired as _CONSENT


def make_domain_node(domain: str):
    """Factory that returns a node function for a given domain."""

    def domain_node(state: QueryState) -> Dict[str, Any]:
        question   = state["question"]
        doc_ids    = state.get("doc_ids") or []
        tenant_id  = state.get("tenant_id") or get_current_tenant_id()

        logger.info(f"[{domain}_node] Running agent")
        try:
            result = _run_domain_agent(domain, question, doc_ids, tenant_id)
        except _CONSENT:
            raise                     # the page asks the user before anything goes to a cloud model
        except Exception as exc:
            logger.error(f"[{domain}_node] error: {exc}", exc_info=True)
            result = AgentResult(
                answer=f"[{domain}] Agent failed: {exc}",
                sources=[],
                steps=[],
                chart_data=None,
                domain=domain,
            )

        return {"agent_results": [result]}

    domain_node.__name__ = f"{domain}_node"
    return domain_node


def merger_node(state: QueryState) -> Dict[str, Any]:
    """
    Collect all AgentResults, run sequential (dependent) agents with enriched
    context, and prepare a combined context string for the synthesizer.

    For now sequential agents get the prior results injected into their question
    as additional context — simple and effective without needing a full DAG runtime.
    """
    results     = list(state.get("agent_results") or [])
    plan        = state.get("plan") or {}
    question    = state["question"]
    doc_ids     = state.get("doc_ids") or []
    tenant_id   = state.get("tenant_id") or get_current_tenant_id()

    # Run any sequential (dependent) domains now
    for dep_group in plan.get("sequential", []):
        for domain in dep_group:
            # Build enriched question with prior context
            prior_ctx = "\n\n".join(
                f"[{r.domain} context]\n{r.answer[:600]}"
                for r in results
                if r.answer
            )
            enriched_q = f"{question}\n\n---\nAdditional context from prior agents:\n{prior_ctx}"
            logger.info(f"[merger_node] Running sequential domain: {domain}")
            try:
                result = _run_domain_agent(domain, enriched_q, doc_ids, tenant_id)
            except _CONSENT:
                raise
            except Exception as exc:
                logger.error(f"[merger_node] sequential {domain} error: {exc}")
                result = AgentResult(
                    answer=f"[{domain}] Agent failed: {exc}",
                    sources=[], steps=[], chart_data=None, domain=domain,
                )
            results.append(result)

    # Merge sources — sources may be strings (filenames) or dicts; normalize both
    seen_source_keys: set = set()
    merged_sources: List = []
    for r in results:
        for src in (r.sources or []):
            if isinstance(src, str):
                key = src
                if key and key not in seen_source_keys:
                    seen_source_keys.add(key)
                    merged_sources.append(src)
            elif isinstance(src, dict):
                key = src.get("doc_id") or src.get("id") or src.get("filename", "")
                if key and key not in seen_source_keys:
                    seen_source_keys.add(key)
                    merged_sources.append(src)

    # Pick chart_data from the first result that has one
    chart_data = next((r.chart_data for r in results if r.chart_data), None)

    return {
        "agent_results": results,   # replace — all results now in one list
        "sources":       merged_sources,
        "chart_data":    chart_data,
    }


def synthesizer_node(state: QueryState) -> Dict[str, Any]:
    """
    Single LLM call to produce a coherent final answer from all domain results.
    """
    results  = state.get("agent_results") or []
    question = state["question"]

    if not results:
        return {"answer": "No results were found for your question."}

    # If only one domain answered, return it directly — but synthesize if it looks like raw chunk text
    unique_domains = {r.domain for r in results if r.answer}
    if len(unique_domains) == 1:
        best = max(results, key=lambda r: len(r.answer or ""))
        raw_answer = best.answer or ""
        # Detect raw chunk dumps: starts with "[p." or "Raw document text"
        _is_raw = raw_answer.startswith("[p.") or raw_answer.startswith("Raw document text")
        if not _is_raw:
            return {"answer": raw_answer or "No answer found."}
        # Synthesize with the LLM
        prompt = (
            f"You are a document intelligence assistant. Use ONLY the document text below to answer the question. "
            f"Be specific and cite numbers, dates, and coverage types you find. "
            f"If something is not in the text, say so.\n\n"
            f"Question: {question}\n\n"
            f"Document text:\n{raw_answer[:6000]}\n\n"
            f"Answer:"
        )
        try:
            model = getattr(app_config, "OLLAMA_MODEL", "mistral")
            resp = llm.chat(role="answer", 
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.1, "num_predict": 800},
            )
            return {"answer": resp["message"]["content"].strip()}
        except Exception as synth_err:
            logger.warning(f"[synthesizer] single-domain synthesis failed: {synth_err}")
            return {"answer": raw_answer}

    # Multi-domain: synthesize
    sections = []
    for r in results:
        if r.answer:
            label = DOMAINS.get(r.domain, {}).get("label", r.domain.title())
            sections.append(f"## {label}\n{r.answer}")

    combined = "\n\n".join(sections)

    prompt = f"""You are a document intelligence assistant. Multiple specialist agents have answered a user question from different angles. Synthesize their findings into a single coherent, well-structured answer. Do not repeat information unnecessarily.

User question: {question}

Specialist findings:
{combined}

Provide a unified answer:"""

    try:
        model = getattr(app_config, "OLLAMA_MODEL", "mistral")
        resp = llm.chat(role="answer", 
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1, "num_predict": 1024},
        )
        answer = resp["message"]["content"].strip()
    except Exception as exc:
        logger.error(f"[synthesizer_node] LLM error: {exc}")
        # Fallback: concatenate sections
        answer = combined

    elapsed = time.time() - (state.get("started_at") or time.time())
    logger.info(f"[synthesizer_node] Done in {elapsed:.1f}s")
    return {"answer": answer}


# ── Fan-out router ─────────────────────────────────────────────────────────────

def route_to_domains(state: QueryState) -> Sequence[Send]:
    """
    Return Send() objects for each parallel domain, triggering true fan-out.
    Sequential domains are handled inside merger_node after parallels complete.
    """
    plan = state.get("plan") or {}
    parallel_domains = plan.get("parallel") or state.get("domains") or ["general"]

    return [
        Send(f"{domain}_node", state)
        for domain in parallel_domains
    ]


# ── Graph construction ────────────────────────────────────────────────────────

def create_query_workflow() -> StateGraph:
    """Build and return the query StateGraph (not yet compiled)."""
    graph = StateGraph(QueryState)

    # Supervisor
    graph.add_node("supervisor_node", supervisor_node)

    # One domain node per agent class + general
    all_domains = list(_AGENT_CLASSES.keys()) + ["general"]
    for domain in all_domains:
        graph.add_node(f"{domain}_node", make_domain_node(domain))

    # Merger and synthesizer
    graph.add_node("merger_node",      merger_node)
    graph.add_node("synthesizer_node", synthesizer_node)

    # Edges
    graph.set_entry_point("supervisor_node")

    # Supervisor → fan-out (conditional edge using Send)
    graph.add_conditional_edges(
        "supervisor_node",
        route_to_domains,
        # All possible targets of the fan-out
        {f"{d}_node": f"{d}_node" for d in all_domains},
    )

    # Every domain node → merger
    for domain in all_domains:
        graph.add_edge(f"{domain}_node", "merger_node")

    # merger → synthesizer → END
    graph.add_edge("merger_node",      "synthesizer_node")
    graph.add_edge("synthesizer_node", END)

    return graph


def compile_query_workflow():
    """Compile and return the runnable query workflow."""
    return create_query_workflow().compile()


# Module-level compiled graph (lazy singleton)
_compiled_query_workflow = None


def get_query_workflow():
    global _compiled_query_workflow
    if _compiled_query_workflow is None:
        _compiled_query_workflow = compile_query_workflow()
    return _compiled_query_workflow


# ── Public entry point ────────────────────────────────────────────────────────

def run_query_workflow(
    question: str,
    doc_ids: Optional[List[str]] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the full query workflow and return the final state.

    Returns a dict with keys: answer, sources, chart_data, domains, agent_results.
    """
    if tenant_id is None:
        tenant_id = get_current_tenant_id()

    initial_state: QueryState = {
        "question":      question,
        "tenant_id":     tenant_id,
        "doc_ids":       doc_ids or [],
        "domains":       [],
        "plan":          {},
        "agent_results": [],
        "answer":        "",
        "sources":       [],
        "chart_data":    None,
        "errors":        [],
        "started_at":    time.time(),
    }

    wf = get_query_workflow()
    final_state = wf.invoke(initial_state)
    return {
        "answer":        final_state.get("answer", ""),
        "sources":       final_state.get("sources", []),
        "chart_data":    final_state.get("chart_data"),
        "domains":       final_state.get("domains", []),
        "agent_results": final_state.get("agent_results", []),
    }
