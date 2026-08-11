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


def create_workflow() -> StateGraph:
    """
    Create linear workflow with Specialist Router Architecture

    Flow: ingest → parse → cleanup → classify → specific_analysis →
          analyze → math_analysis → search_prep → quality_check →
          insight → analytics → calculate_metrics → entity_linking → finalize
    """

    workflow = StateGraph(DocumentState)

    # Add nodes
    workflow.add_node("ingest", ingest_node)
    workflow.add_node("precheck", precheck_node)
    workflow.add_node("extract_archive", extract_archive_node)
    workflow.add_node("parse", parse_node)
    workflow.add_node("cleanup", cleanup_node)
    #workflow.add_node("classify", classify_node)
    workflow.add_node("specific_analysis", document_specific_analysis_node)
    workflow.add_node("analyze", analyze_node)
    #workflow.add_node("math_analysis", math_analysis_node)  # Math Path
    workflow.add_node("search_prep", search_prep_node)
    workflow.add_node("chunk_and_embed", chunk_and_embed_node)
    workflow.add_node("quality_check", quality_check_node)
    workflow.add_node("insight", insight_node)
    workflow.add_node("calculate_metrics", metrics_node)
    workflow.add_node("entity_linking", entity_linking_node)  # Link documents
    workflow.add_node("finalize", finalize_node)

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
    #workflow.add_edge("cleanup", "classify")
    workflow.add_edge("cleanup", "specific_analysis")  # Document-specific analyzers
    workflow.add_edge("specific_analysis", "analyze")  # AI summary/key points
    workflow.add_edge("analyze", "search_prep")  # ✅ Math Path (Pandas calculations)
    #workflow.add_edge("math_analysis", "search_prep")  # ✅ Then embeddings
    workflow.add_edge("search_prep", "chunk_and_embed")
    workflow.add_edge("chunk_and_embed", "quality_check")
    workflow.add_edge("quality_check", "insight")
    workflow.add_edge("insight", "calculate_metrics")
    workflow.add_edge("calculate_metrics", "entity_linking")  # ✅ Entity linking & document relationships
    workflow.add_edge("entity_linking", "finalize")

    workflow.add_edge("finalize", END)

    logger.info("✅ Specialist Router workflow created: 14 nodes, 0 conditionals")
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
