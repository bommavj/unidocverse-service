"""
UniDocVerse MCP Tools
Interactive tools for Claude to query and interact with documents
"""

import logging
from typing import Any, Dict, List, Optional
from pathlib import Path

from app.core import config
from app.core.database import SessionLocal
from app.services.document_service import DocumentService

logger = logging.getLogger(__name__)


# ============================================================================
# MCP TOOL: Natural Language Document Search
# ============================================================================

async def search_documents_natural(
        query: str,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Search documents using natural language query with semantic understanding

    Examples:
        - "Find all invoices from last month"
        - "Show me expense reports"
        - "Documents about Q3 sales"
        - "Spreadsheets with budget data"

    Args:
        query: Natural language search query
        limit: Maximum results to return
        filters: Optional filters (doc_type, date_range, etc.)

    Returns:
        List of matching documents with relevance scores
    """
    try:
        logger.info(f"🔍 Natural language search: {query}")

        db = SessionLocal()

        # Use semantic search
        results = DocumentService.semantic_search(db, query, limit)

        # Apply filters if provided
        if filters:
            if filters.get('doc_type'):
                results = [r for r in results if r['doc_type'] == filters['doc_type']]

            if filters.get('min_similarity'):
                results = [r for r in results if r['similarity'] >= filters['min_similarity']]

        db.close()

        logger.info(f"✅ Found {len(results)} documents")
        return results

    except Exception as e:
        logger.error(f"❌ Search failed: {e}")
        return []


# ============================================================================
# MCP TOOL: Ask Question About Any Document
# ============================================================================

async def ask_question(question: str, limit: int = 5) -> Dict[str, Any]:
    """
    Answer questions about documents using RAG (Retrieval Augmented Generation)

    Examples:
        - "What's the total unit price for product phone?"
        - "How many invoices do I have?"
        - "What's the total amount across all documents?"

    Args:
        question: Natural language question
        limit: Number of documents to search

    Returns:
        AI-generated answer with supporting documents
    """
    try:
        logger.info(f"💬 Answering question: {question}")

        from app.core.database import SessionLocal
        from app.models.document import Document
        import ollama

        db = SessionLocal()

        # Step 1: Extract key terms from question
        key_terms = _extract_key_terms(question)
        logger.info(f"🔑 Key terms: {key_terms}")

        # Step 2: Search for relevant documents
        relevant_docs = []

        if key_terms:
            # Search by keywords in text and metadata
            search_term = ' '.join(key_terms)

            docs = db.query(Document) \
                .filter(Document.status == 'completed') \
                .filter(
                (Document.raw_text.ilike(f'%{search_term}%')) |
                (Document.summary.ilike(f'%{search_term}%')) |
                (Document.filename.ilike(f'%{search_term}%'))
            ) \
                .limit(limit) \
                .all()

            relevant_docs = docs

        # If no docs found by keyword, try semantic search
        if not relevant_docs:
            logger.info("📊 Trying semantic search...")
            semantic_results = await search_documents_natural(question, limit)

            if semantic_results:
                doc_ids = [r['id'] for r in semantic_results]
                relevant_docs = db.query(Document) \
                    .filter(Document.id.in_(doc_ids)) \
                    .all()

        db.close()

        if not relevant_docs:
            return {
                "answer": "I couldn't find any documents related to your question.",
                "confidence": 0.0,
                "sources": []
            }

        logger.info(f"📄 Found {len(relevant_docs)} relevant documents")

        # Step 3: Extract relevant context from documents
        context = _build_context(relevant_docs, question)

        # Step 4: Generate answer using AI
        answer_data = _generate_answer(question, context, relevant_docs)

        return answer_data

    except Exception as e:
        logger.error(f"❌ Question answering failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "answer": f"Error processing question: {str(e)}",
            "confidence": 0.0,
            "sources": []
        }


def _extract_key_terms(question: str) -> List[str]:
    """Extract key search terms from question"""
    import re

    # Remove question words
    stop_words = {'what', 'how', 'when', 'where', 'who', 'why', 'which', 'is', 'are', 'the', 'a', 'an', 'for', 'in',
                  'on'}

    # Extract words
    words = re.findall(r'\b[a-z]{3,}\b', question.lower())

    # Filter stop words
    key_terms = [w for w in words if w not in stop_words]

    return key_terms[:5]  # Top 5 terms


def _build_context(docs: List, question: str) -> str:
    """Build context from relevant documents"""
    context_parts = []

    for i, doc in enumerate(docs, 1):
        doc_context = f"\n--- Document {i}: {doc.filename} ---\n"

        # Add summary if available
        if doc.summary:
            doc_context += f"Summary: {doc.summary}\n\n"

        # Add structured data if available
        if doc.doc_metadata and isinstance(doc.doc_metadata, dict):
            structured = doc.doc_metadata.get('structured_data', {})

            if structured:
                doc_context += "Structured Data:\n"

                # Add line items if present (invoices, receipts)
                if 'line_items' in structured:
                    doc_context += "Line Items:\n"
                    for item in structured['line_items'][:10]:  # First 10 items
                        doc_context += f"  - {item}\n"

                # Add transactions if present (bank statements)
                if 'transactions' in structured:
                    doc_context += "Transactions:\n"
                    for txn in structured['transactions'][:10]:
                        doc_context += f"  - {txn}\n"

                # Add other key fields
                for key in ['total_amount', 'invoice_number', 'date', 'vendor', 'total_spent', 'total_income']:
                    if key in structured:
                        doc_context += f"{key}: {structured[key]}\n"

        # Add relevant text snippet
        if doc.raw_text:
            # Find relevant snippet around key terms
            snippet = _find_relevant_snippet(doc.raw_text, question)
            if snippet:
                doc_context += f"\nRelevant excerpt:\n{snippet}\n"

        context_parts.append(doc_context)

    return "\n\n".join(context_parts)


def _find_relevant_snippet(text: str, question: str, context_window: int = 500) -> str:
    """Find most relevant snippet from text"""
    import re

    # Extract key terms from question
    key_terms = _extract_key_terms(question)

    if not key_terms:
        return text[:1000]  # First 1000 chars

    # Find first occurrence of any key term
    text_lower = text.lower()
    positions = []

    for term in key_terms:
        pos = text_lower.find(term)
        if pos != -1:
            positions.append(pos)

    if not positions:
        return text[:1000]

    # Get snippet around first key term
    start_pos = max(0, min(positions) - context_window // 2)
    end_pos = min(len(text), start_pos + context_window)

    snippet = text[start_pos:end_pos]

    # Clean up
    if start_pos > 0:
        snippet = "..." + snippet
    if end_pos < len(text):
        snippet = snippet + "..."

    return snippet


def _generate_answer(question: str, context: str, docs: List) -> Dict[str, Any]:
    """Generate answer using AI"""
    import ollama
    import json

    prompt = f"""You are a helpful AI assistant analyzing documents.

Question: {question}

Context from relevant documents:
{context[:4000]}  

Based on the context above, answer the question. Be specific and cite information from the documents.

If the question asks for numbers, calculations, or totals:
1. Extract all relevant data from the structured data and text
2. Perform the calculation
3. Show your work

If you cannot find the answer in the context, say so clearly.

Answer:"""

    try:
        client = ollama.Client()
        response = client.generate(
            model=config.model,
            prompt=prompt
        )

        answer = response['response'].strip()

        # Build sources
        sources = [
            {
                "filename": doc.filename,
                "doc_type": doc.doc_type or 'unknown',
                "summary": doc.summary or "No summary available"
            }
            for doc in docs
        ]

        return {
            "answer": answer,
            "confidence": 0.85,
            "sources": sources,
            "documents_searched": len(docs)
        }

    except Exception as e:
        logger.error(f"AI generation failed: {e}")
        return {
            "answer": "I found relevant documents but couldn't generate an answer.",
            "confidence": 0.5,
            "sources": [{"filename": doc.filename} for doc in docs]
        }

# async def ask_question(
#         query: str,
#         doc_id: Optional[str] = None,
#         context: Optional[str] = None
# ) -> Dict[str, Any]:
#     """
#     Ask a question about a specific document or across all documents
#
#     Examples:
#         - "What's the total on invoice ABC?" (specific doc)
#         - "What are my top 3 expenses this month?" (across docs)
#         - "Compare Q2 vs Q3 revenue" (multiple docs)
#
#     Args:
#         query: Question to ask
#         doc_id: Specific document ID (optional)
#         context: Additional context (optional)
#
#     Returns:
#         AI answer with sources
#     """
#     try:
#         import ollama
#
#         db = SessionLocal()
#
#         # If specific doc_id provided
#         if doc_id:
#             doc = DocumentService.get_document_by_id(db, doc_id)
#             if not doc or not doc.raw_text:
#                 return {"error": "Document not found or has no content"}
#
#             context_text = f"""Document: {doc.filename}
# Type: {doc.doc_type}
# Summary: {doc.summary}
#
# Full Content:
# {doc.raw_text[:15000]}"""
#
#         # If no doc_id, search for relevant docs
#         else:
#             relevant_docs = DocumentService.semantic_search(db, query, limit=3)
#
#             if not relevant_docs:
#                 return {"answer": "I couldn't find any relevant documents for this question."}
#
#             # Build context from multiple docs
#             context_text = "Relevant Documents:\n\n"
#             sources = []
#
#             for i, doc_info in enumerate(relevant_docs, 1):
#                 doc = DocumentService.get_document_by_id(db, doc_info['id'])
#                 if doc and doc.raw_text:
#                     context_text += f"Document {i}: {doc.filename}\n"
#                     context_text += f"Type: {doc.doc_type}\n"
#                     context_text += f"Summary: {doc.summary}\n"
#                     context_text += f"Content:\n{doc.raw_text[:5000]}\n\n"
#                     sources.append({
#                         "id": str(doc.id),
#                         "filename": doc.filename,
#                         "relevance": doc_info['similarity_percent']
#                     })
#
#         db.close()
#
#         # Add user context if provided
#         if context:
#             context_text += f"\nAdditional Context: {context}\n"
#
#         # Ask Ollama
#         prompt = f"""{context_text}
#
# Question: {query}
#
# Provide a clear, specific answer based on the document(s) above. Cite specific details and numbers when available.
#
# Answer:"""
#
#         client = ollama.Client()
#         response = client.generate(model="phi3:mini", prompt=prompt)
#
#         answer = response['response'].strip()
#
#         result = {
#             "question": query,
#             "answer": answer,
#             "sources": sources if not doc_id else [{"id": doc_id, "filename": doc.filename}]
#         }
#
#         logger.info(f"✅ Generated answer for: {query}")
#         return result
#
#     except Exception as e:
#         logger.error(f"❌ Question answering failed: {e}")
#         return {"error": str(e)}


# ============================================================================
# MCP TOOL: Extract Financial Data
# ============================================================================

async def extract_financial_data(
        doc_id: Optional[str] = None,
        query: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extract financial data from documents (amounts, totals, line items)

    Examples:
        - Extract from specific invoice
        - Find all expenses in date range
        - Calculate total revenue from invoices

    Args:
        doc_id: Specific document (optional)
        query: Search query for multiple docs (optional)

    Returns:
        Extracted financial data with line items
    """
    try:
        import ollama
        import json
        import re

        db = SessionLocal()

        # Get document(s)
        if doc_id:
            doc = DocumentService.get_document_by_id(db, doc_id)
            documents = [doc] if doc else []
        elif query:
            search_results = DocumentService.semantic_search(db, query, limit=10)
            doc_ids = [r['id'] for r in search_results]
            documents = [DocumentService.get_document_by_id(db, did) for did in doc_ids]
            documents = [d for d in documents if d]
        else:
            db.close()
            return {"error": "Must provide doc_id or query"}

        db.close()

        # Extract financial data using AI
        all_financial_data = []

        for doc in documents:
            if not doc.raw_text:
                continue

            prompt = f"""Extract all financial data from this document.

Document: {doc.filename}
Type: {doc.doc_type}

Content:
{doc.raw_text[:10000]}

Extract and return JSON with:
{{
  "currency": "USD",
  "total_amount": 999.99,
  "line_items": [
    {{"description": "Item 1", "amount": 99.99}},
    ...
  ],
  "taxes": 10.00,
  "subtotal": 989.99,
  "dates": {{"invoice_date": "2024-01-01", "due_date": "2024-02-01"}}
}}

Financial Data:"""

            client = ollama.Client()
            response = client.generate(model=config.model, prompt=prompt)

            response_text = response['response'].strip()
            response_text = re.sub(r'```json\s*', '', response_text)
            response_text = re.sub(r'```\s*', '', response_text)

            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                financial_data = json.loads(json_match.group())
                financial_data['document_id'] = str(doc.id)
                financial_data['document_name'] = doc.filename
                all_financial_data.append(financial_data)

        # Calculate totals across all documents
        total_sum = sum(item.get('total_amount', 0) for item in all_financial_data)

        return {
            "documents_analyzed": len(all_financial_data),
            "total_amount": total_sum,
            "details": all_financial_data
        }

    except Exception as e:
        logger.error(f"❌ Financial extraction failed: {e}")
        return {"error": str(e)}


# ============================================================================
# MCP TOOL: Compare Documents
# ============================================================================

async def compare_documents(
        doc_ids: List[str],
        comparison_type: str = "general"
) -> Dict[str, Any]:
    """
    Compare multiple documents side-by-side

    Examples:
        - Compare two invoices
        - Compare Q1 vs Q2 reports
        - Find differences between versions

    Args:
        doc_ids: List of document IDs to compare
        comparison_type: Type of comparison (general, financial, content)

    Returns:
        Comparison analysis
    """
    try:
        import ollama

        if len(doc_ids) < 2:
            return {"error": "Need at least 2 documents to compare"}

        db = SessionLocal()
        documents = [DocumentService.get_document_by_id(db, did) for did in doc_ids]
        documents = [d for d in documents if d and d.raw_text]
        db.close()

        if len(documents) < 2:
            return {"error": "Could not load documents"}

        # Build comparison context
        context = "Documents to Compare:\n\n"
        for i, doc in enumerate(documents, 1):
            context += f"Document {i}: {doc.filename}\n"
            context += f"Type: {doc.doc_type}\n"
            context += f"Summary: {doc.summary}\n"
            context += f"Content: {doc.raw_text[:5000]}\n\n"

        prompt = f"""{context}

Compare these documents and identify:
1. Key similarities
2. Major differences
3. Important insights from comparison

Comparison Analysis:"""

        client = ollama.Client()
        response = client.generate(model=config.model, prompt=prompt)

        return {
            "documents_compared": [{"id": str(d.id), "filename": d.filename} for d in documents],
            "comparison": response['response'].strip()
        }

    except Exception as e:
        logger.error(f"❌ Comparison failed: {e}")
        return {"error": str(e)}


# ============================================================================
# MCP TOOL: Generate Document Report
# ============================================================================

async def generate_report(
        query: str,
        format: str = "summary"
) -> str:
    """
    Generate a comprehensive report across documents

    Examples:
        - "Monthly expense report"
        - "Q3 sales summary"
        - "All invoices overview"

    Args:
        query: What to report on
        format: Report format (summary, detailed, financial)

    Returns:
        Generated report
    """
    try:
        import ollama

        # Search for relevant documents
        db = SessionLocal()
        results = DocumentService.semantic_search(db, query, limit=20)

        # Get full documents
        documents = []
        for result in results:
            doc = DocumentService.get_document_by_id(db, result['id'])
            if doc:
                documents.append(doc)

        db.close()

        if not documents:
            return "No relevant documents found for this report."

        # Build report context
        context = f"Report Request: {query}\n\n"
        context += f"Found {len(documents)} relevant documents:\n\n"

        for doc in documents:
            context += f"- {doc.filename} ({doc.doc_type})\n"
            context += f"  Summary: {doc.summary}\n\n"

        prompt = f"""{context}

Generate a comprehensive {format} report based on these documents. Include:
- Executive summary
- Key findings
- Important numbers and metrics
- Actionable insights

Report:"""

        client = ollama.Client()
        response = client.generate(model=config.model, prompt=prompt)

        return response['response'].strip()

    except Exception as e:
        logger.error(f"❌ Report generation failed: {e}")
        return f"Error: {str(e)}"


# ============================================================================
# MCP TOOL: List Documents with Filters
# ============================================================================

async def list_documents_filtered(
        doc_type: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50
) -> List[Dict[str, Any]]:
    """
    List documents with various filters

    Args:
        doc_type: Filter by type (invoice, spreadsheet, etc.)
        status: Filter by status (completed, processing, etc.)
        date_from: Start date (ISO format)
        date_to: End date (ISO format)
        limit: Maximum results

    Returns:
        Filtered list of documents
    """
    try:
        db = SessionLocal()

        # Get all documents
        documents = DocumentService.get_documents(db, 0, 1000)

        # Apply filters
        if doc_type:
            documents = [d for d in documents if d.doc_type == doc_type]

        if status:
            documents = [d for d in documents if d.status == status]

        if date_from:
            from datetime import datetime
            date_from_dt = datetime.fromisoformat(date_from)
            documents = [d for d in documents if d.created_at >= date_from_dt]

        if date_to:
            from datetime import datetime
            date_to_dt = datetime.fromisoformat(date_to)
            documents = [d for d in documents if d.created_at <= date_to_dt]

        # Limit results
        documents = documents[:limit]

        db.close()

        return [
            {
                "id": str(doc.id),
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "status": doc.status,
                "summary": doc.summary,
                "created_at": doc.created_at.isoformat()
            }
            for doc in documents
        ]

    except Exception as e:
        logger.error(f"❌ List filtered failed: {e}")
        return []