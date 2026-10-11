import logging
import uuid

from app.agents.state import DocumentState
from app.core.guardrails import AgentLoopGuard, GuardrailViolation

logger = logging.getLogger(__name__)


def should_do_manual_review(state: DocumentState) -> str:
    """
    Decide if document needs manual review

    Returns:
        "manual_review" - Send to manual review
        "continue" - Continue processing
    """

    # TEMPORARY: Always skip manual review for testing
    logger.info(f"⏭️ [ManualReview] Skipping manual review (testing mode)")
    return "continue"

    # ORIGINAL CODE (will restore later):
    # classification = state.get("classification", {})
    # confidence = classification.get("confidence", 1.0)
    #
    # if confidence < 0.7:
    #     logger.warning(f"⚠️ Low confidence ({confidence:.2%}), routing to manual review")
    #     return "manual_review"
    #
    # logger.info(f"✅ High confidence ({confidence:.2%}), continuing processing")
    # return "continue"


# def should_do_manual_review(state: DocumentState) -> str:
#     """
#     Routing function: Decide if document needs manual review
#
#     Returns:
#         "manual_review" if review needed
#         "continue" if processing can continue
#     """
#     needs_review = state.get("needs_manual_review", False)
#     confidence = state.get("confidence_score", 1.0)
#
#     if needs_review or confidence < 0.5:
#         logger.info(f"📋 Routing to manual review (confidence: {confidence:.1%})")
#         return "manual_review"
#     else:
#         logger.info(f"✅ Confidence acceptable ({confidence:.1%}), continuing")
#         return "continue"


def should_reprocess(state: DocumentState) -> str:
    """Decide if document needs reprocessing"""

    # TEMPORARY: Disable reprocessing
    quality = state.get("quality_issues", [])

    if quality:
        logger.warning(f"⚠️ Found {len(quality)} quality issues, but continuing anyway")
    else:
        logger.info(f"✅ No quality issues found")

    return "continue"


import asyncio
import logging
from langgraph.graph import StateGraph, END
from app.agents.state import DocumentState
from app.agents.langgraph_agents import (
    ingest_node,
    parse_node,
    cleanup_node,
    analyze_node,
    search_prep_node,
    quality_check_node,
    insight_node,
    metrics_node,
    document_specific_analysis_node,
    math_analysis_node,
    entity_linking_node,
    finalize_node, chunk_and_embed_node,
)
from app.agents.precheck_node import precheck_node
from app.agents.extract_archive_node import extract_archive_node

logger = logging.getLogger(__name__)


def _async_node(fn):
    """
    Wrap a sync node function so LangGraph runs it in a thread pool via
    asyncio.to_thread().  This lets the event loop schedule multiple parallel
    branches concurrently instead of blocking on each one sequentially.
    """
    async def _wrapper(state):
        return await asyncio.to_thread(fn, state)
    _wrapper.__name__ = fn.__name__
    return _wrapper


def create_workflow() -> StateGraph:
    """
    Create workflow with parallel fan-out after cleanup.

    Flow: ingest → precheck → parse → cleanup
            ├─ specific_analysis → analyze → insight ─┐
            ├─ chunk_and_embed (heavy)                  ├─ entity_linking → finalize
            ├─ search_prep                              │
            ├─ quality_check                            │
            └─ calculate_metrics ──────────────────────┘
    """

    workflow = StateGraph(DocumentState)

    # Sequential nodes (no benefit from threading — either trivial or must be serial)
    workflow.add_node("ingest", ingest_node)
    workflow.add_node("precheck", precheck_node)
    workflow.add_node("extract_archive", extract_archive_node)
    workflow.add_node("parse", parse_node)
    workflow.add_node("cleanup", cleanup_node)
    workflow.add_node("finalize", finalize_node)

    # Parallel branches — wrapped so ainvoke can run them concurrently
    workflow.add_node("specific_analysis", _async_node(document_specific_analysis_node))
    workflow.add_node("analyze",           _async_node(analyze_node))
    workflow.add_node("insight",           _async_node(insight_node))
    workflow.add_node("chunk_and_embed",   _async_node(chunk_and_embed_node))
    workflow.add_node("search_prep",       _async_node(search_prep_node))
    workflow.add_node("quality_check",     _async_node(quality_check_node))
    workflow.add_node("calculate_metrics", _async_node(metrics_node))
    workflow.add_node("entity_linking",    _async_node(entity_linking_node))

    # Set entry point
    workflow.set_entry_point("ingest")

    # Linear edges - Specialist Router Architecture
    workflow.add_edge("ingest", "precheck")

    # After precheck: password-protected → finalize | else → parse
    def _after_precheck(state: DocumentState) -> str:
        if state.get("password_required"):
            return "finalize"
        return "parse"

    workflow.add_conditional_edges(
        "precheck", _after_precheck,
        {"finalize": "finalize", "parse": "parse"}
    )
    workflow.add_edge("parse", "cleanup")

    # ── Parallel fan-out from cleanup ────────────────────────────────────────
    # cleanup produces: cleaned_text, doc_metadata, classification
    #
    # Branch 1 (LLM chain):  cleanup → specific_analysis → analyze → insight
    #                                 → entity_linking → finalize → END
    # Branch 2 (embedding):  cleanup → chunk_and_embed → END  (~37s, stores to DB directly)
    # Branch 3 (keywords):   cleanup → search_prep     → END  (~0s, pure Python)
    # Branch 4 (quality):    cleanup → quality_check   → END  (~0s, pure Python)
    # Branch 5 (metrics):    cleanup → calculate_metrics → END (~0s, pure Python)
    #
    # Branches 2-5 write their state keys immediately and terminate at END.
    # LangGraph merges their state into the shared state graph; entity_linking
    # and finalize (on branch 1) read those keys from the merged state without
    # needing a formal edge from each branch. This eliminates the double-fire
    # of entity_linking that happens when LangGraph 0.2.x triggers a fan-in
    # node once per arriving super-step rather than once after all arrive.
    #
    # Net effect: chunk_and_embed (~37s) and the LLM chain (~46s) run
    # concurrently; finalize sees all branch outputs because the fast branches
    # always complete before the LLM chain does.

    # Branch 1 — LLM chain (sequential; only path to finalize)
    workflow.add_edge("cleanup", "specific_analysis")
    workflow.add_edge("specific_analysis", "analyze")
    workflow.add_edge("analyze", "insight")
    workflow.add_edge("insight", "entity_linking")
    workflow.add_edge("entity_linking", "finalize")
    workflow.add_edge("finalize", END)

    # Branches 2-5 — terminate at END after writing their state keys
    workflow.add_edge("cleanup", "chunk_and_embed")
    workflow.add_edge("cleanup", "search_prep")
    workflow.add_edge("cleanup", "quality_check")
    workflow.add_edge("cleanup", "calculate_metrics")
    workflow.add_edge("chunk_and_embed", END)
    workflow.add_edge("search_prep", END)
    workflow.add_edge("quality_check", END)
    workflow.add_edge("calculate_metrics", END)

    logger.info("✅ Specialist Router workflow created: 14 nodes, 1 critical path + 4 independent branches")
    logger.info("   📊 Math Path: math_analysis (Pandas)")
    logger.info("   🤖 Logic Path: analyze (AI)")
    logger.info("   🔗 Entity Linking: entity_linking")

    return workflow


def initialize_workflow():
    """Initialize and compile workflow"""
    logger.info("🔧 Initializing workflow...")
    workflow = create_workflow()
    compiled = workflow.compile()
    logger.info("✅ Workflow compiled successfully")
    return compiled


def compile_workflow():
    """Compile the workflow into a runnable app"""
    workflow = create_workflow()
    app = workflow.compile()
    return app


def run_pipeline(state: DocumentState, session_id: str = ""):
    """Run the compiled workflow for a single document with loop guardrails."""
    session_id = session_id or str(uuid.uuid4())
    guard = AgentLoopGuard(session_id=session_id)
    app = compile_workflow()
    try:
        # LangGraph invokes nodes sequentially; tick once per top-level invoke
        guard.tick(1)
        result = app.invoke(state)
        return result
    except GuardrailViolation as e:
        logger.warning("Agent loop guardrail: %s — %s", e.rule, e.message)
        return {"errors": [{"node": "guardrail", "error": e.message, "rule": e.rule}]}
