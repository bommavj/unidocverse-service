"""
MCP REST API Endpoints
Exposes MCP tools as REST endpoints for the frontend
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from app.mcp_server import tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


# ============================================================================
# Request/Response Models
# ============================================================================

class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    filters: Optional[Dict[str, Any]] = None


class QuestionRequest(BaseModel):
    query: str
    doc_id: Optional[str] = None
    context: Optional[str] = None


class FinancialDataRequest(BaseModel):
    doc_id: Optional[str] = None
    query: Optional[str] = None


class CompareRequest(BaseModel):
    doc_ids: List[str]
    comparison_type: str = "general"


class ReportRequest(BaseModel):
    query: str
    format: str = "summary"


class ListRequest(BaseModel):
    doc_type: Optional[str] = None
    status: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    limit: int = 50


# ============================================================================
# MCP Endpoints
# ============================================================================

@router.post("/search")
async def natural_language_search(request: SearchRequest):
    """
    Natural language document search

    Example queries:
    - "Find all invoices from last month"
    - "Show me expense reports"
    - "Documents about Q3 sales"
    """
    try:
        results = await tools.search_documents_natural(
            query=request.query,
            limit=request.limit,
            filters=request.filters
        )

        return {
            "query": request.query,
            "results": results,
            "count": len(results)
        }

    except Exception as e:
        logger.error(f"❌ Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ask")
async def ask_question(request: QuestionRequest):
    """
    Ask a question about documents

    Examples:
    - "What's the total on this invoice?"
    - "What are my top 3 expenses?"
    - "Compare Q2 vs Q3 revenue"
    """
    try:
        result = await tools.ask_question(
            query=request.query,
            doc_id=request.doc_id,
            context=request.context
        )

        return result

    except Exception as e:
        logger.error(f"❌ Question failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-financial")
async def extract_financial(request: FinancialDataRequest):
    """
    Extract financial data from documents

    Returns amounts, line items, totals, etc.
    """
    try:
        result = await tools.extract_financial_data(
            doc_id=request.doc_id,
            query=request.query
        )

        return result

    except Exception as e:
        logger.error(f"❌ Financial extraction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compare")
async def compare_documents(request: CompareRequest):
    """
    Compare multiple documents side-by-side
    """
    try:
        result = await tools.compare_documents(
            doc_ids=request.doc_ids,
            comparison_type=request.comparison_type
        )

        return result

    except Exception as e:
        logger.error(f"❌ Comparison failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-report")
async def generate_report(request: ReportRequest):
    """
    Generate comprehensive report across documents
    """
    try:
        report = await tools.generate_report(
            query=request.query,
            format=request.format
        )

        return {
            "query": request.query,
            "report": report
        }

    except Exception as e:
        logger.error(f"❌ Report generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/list-filtered")
async def list_filtered(request: ListRequest):
    """
    List documents with filters
    """
    try:
        documents = await tools.list_documents_filtered(
            doc_type=request.doc_type,
            status=request.status,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit
        )

        return {
            "documents": documents,
            "count": len(documents)
        }

    except Exception as e:
        logger.error(f"❌ List filtered failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/smart-search")
async def smart_search(query: str, limit: int = 10):
    """Smart search with intent detection"""
    try:
        logger.info(f"🔍 Smart search: {query}")

        # Detect intent
        intent_result = QueryRouter.detect_intent(query)
        intent = intent_result['intent']

        logger.info(f"🎯 Detected intent: {intent} (confidence: {intent_result['confidence']})")

        result_data = None

        # Route based on intent
        if intent == 'question':
            # Use enhanced Q&A
            result_data = await tools.ask_question(query, limit)

        elif intent == 'search':
            # Natural language search
            result_data = await tools.search_documents_natural(query, limit)

        elif intent == 'financial':
            # Financial extraction
            # First find relevant documents
            search_results = await tools.search_documents_natural(query, limit)
            if search_results:
                doc_ids = [r['id'] for r in search_results]
                result_data = await tools.extract_financial_data(doc_ids)
            else:
                result_data = {"error": "No financial documents found"}

        elif intent == 'comparison':
            # Document comparison
            search_results = await tools.search_documents_natural(query, limit=2)
            if len(search_results) >= 2:
                result_data = await tools.compare_documents(
                    search_results[0]['id'],
                    search_results[1]['id']
                )
            else:
                result_data = {"error": "Need at least 2 documents to compare"}

        elif intent == 'bank_statement':
            # Bank statement analysis
            search_results = await tools.search_documents_natural("bank statement", 1)
            if search_results:
                doc_id = search_results[0]['id']

                # Extract month from query
                import re
                month_match = re.search(
                    r'(january|february|march|april|may|june|july|august|september|october|november|december)',
                    query.lower()
                )
                month = month_match.group(1) if month_match else None

                result_data = await tools.analyze_bank_statement(doc_id, month)
            else:
                result_data = {"error": "No bank statement found"}

        else:  # list or unknown
            result_data = await tools.search_documents_natural(query, limit)

        # Save to search history
        from app.core.database import SessionLocal
        from app.services.search_history_service import SearchHistoryService

        db = SessionLocal()

        results_preview = {}
        result_count = 0

        if result_data:
            if isinstance(result_data, list):
                results_preview = {"items": result_data[:3]}
                result_count = len(result_data)
            elif isinstance(result_data, dict):
                if 'answer' in result_data:
                    results_preview = {"answer": result_data['answer'][:200]}
                    result_count = len(result_data.get('sources', []))
                else:
                    results_preview = {k: str(v)[:100] for k, v in list(result_data.items())[:3]}
                    result_count = 1

        SearchHistoryService.add_search(
            db=db,
            query=query,
            intent=intent,
            result_count=result_count,
            results_preview=results_preview
        )

        db.close()

        return {
            "type": intent,
            "intent": intent_result,
            "data": result_data,
            "query": query
        }

    except Exception as e:
        logger.error(f"❌ Smart search failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))