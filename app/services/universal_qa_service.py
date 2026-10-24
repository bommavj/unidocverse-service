"""
Universal Document Q&A System
Works with ANY document type: Medical, Financial, Legal, Technical, etc.

Author: Vijay Kumar Bomma
"""
import ollama
from typing import Dict, List, Any, Optional
import logging
import json

logger = logging.getLogger(__name__)


class UniversalDocumentQA:
    """
    Universal Q&A system that works with any document type
    Intelligently uses both raw text and structured analysis when available
    """

    def __init__(self, ollama_model: str = "mistral:latest"):
        self.model = ollama_model
        self.client = ollama.Client()

    def ask(
            self,
            question: str,
            raw_text: str,
            analysis_results: Optional[Dict] = None,
            metadata: Optional[Dict] = None,
            conversation_history: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Universal Q&A - works with any document

        Args:
            question: User's question
            raw_text: Full document text (NO TRUNCATION!)
            analysis_results: Optional structured analysis (from any analyzer)
            metadata: Optional document metadata (filename, type, etc.)
            conversation_history: Previous Q&A exchanges

        Returns:
            Dict with answer and confidence
        """
        logger.info(f"🤔 Question: {question}")

        # Build comprehensive context
        context = self._build_universal_context(
            raw_text=raw_text,
            analysis_results=analysis_results,
            metadata=metadata,
            question=question
        )

        # Add conversation history
        if conversation_history:
            context += self._format_history(conversation_history)

        # Generate answer
        answer = self._generate_answer(question, context)

        return {
            "question": question,
            "answer": answer,
            "has_structured_data": bool(analysis_results and analysis_results.get('success')),
            "context_size": len(context)
        }

    def _build_universal_context(
            self,
            raw_text: str,
            analysis_results: Optional[Dict],
            metadata: Optional[Dict],
            question: str
    ) -> str:
        """Build context intelligently based on what's available"""

        context = "DOCUMENT ANALYSIS:\n"
        context += "=" * 70 + "\n\n"

        # Add metadata if available
        if metadata:
            context += "DOCUMENT INFO:\n"
            for key, value in metadata.items():
                if value:
                    context += f"- {key}: {value}\n"
            context += "\n"

        # Add structured analysis if available (from ANY analyzer)
        if analysis_results and analysis_results.get('success'):
            context += self._format_structured_analysis(analysis_results, question)

        # ALWAYS add full raw text (no truncation!)
        context += "\nFULL DOCUMENT CONTENT:\n"
        context += "=" * 70 + "\n"
        context += raw_text  # ← Complete text, no [:10000] truncation!
        context += "\n" + "=" * 70 + "\n\n"

        return context

    def _format_structured_analysis(self, analysis_results: Dict, question: str) -> str:
        """
        Format structured analysis from ANY analyzer
        Works with: Bank statements, Medical records, Sales data, etc.
        """
        formatted = "STRUCTURED ANALYSIS AVAILABLE:\n"
        formatted += "-" * 70 + "\n"

        # Record count
        if 'total_records' in analysis_results:
            formatted += f"Total Records: {analysis_results['total_records']}\n"

        # Record type (medical, bank, sales, etc.)
        if 'record_type' in analysis_results:
            formatted += f"Type: {analysis_results['record_type']}\n"

        # Quick summary
        if 'summary' in analysis_results:
            formatted += f"\nSummary: {analysis_results['summary']}\n"

        # Key insights
        if 'insights' in analysis_results and analysis_results['insights']:
            formatted += f"\nKey Insights ({len(analysis_results['insights'])}):\n"
            for insight in analysis_results['insights'][:5]:
                formatted += f"  • {insight}\n"

        # Check if question needs detailed statistics
        question_lower = question.lower()
        needs_stats = any(kw in question_lower for kw in [
            'how many', 'count', 'total', 'average', 'sum',
            'by', 'per', 'each', 'distribution', 'breakdown'
        ])

        if needs_stats and 'statistics' in analysis_results:
            formatted += "\nDETAILED STATISTICS:\n"
            formatted += json.dumps(analysis_results['statistics'], indent=2)
            formatted += "\n"

        # For "list all" or "who are" questions, include records
        needs_records = any(kw in question_lower for kw in [
            'list all', 'show all', 'get all', 'who are', 'which'
        ])

        if needs_records and 'records' in analysis_results:
            records = analysis_results['records']
            formatted += f"\nALL RECORDS ({len(records)} total):\n"
            formatted += json.dumps(records, indent=2)
            formatted += "\n"

        formatted += "-" * 70 + "\n\n"
        return formatted

    def _format_history(self, history: List[Dict]) -> str:
        """Format conversation history"""
        formatted = "PREVIOUS CONVERSATION:\n"
        formatted += "-" * 70 + "\n"

        for i, exchange in enumerate(history[-5:], 1):
            formatted += f"\nQ{i}: {exchange.get('question', '')}\n"
            formatted += f"A{i}: {exchange.get('answer', '')}\n"

        formatted += "-" * 70 + "\n\n"
        return formatted

    def _generate_answer(self, question: str, context: str, doc_type: str) -> str:
        """
        Document-type-aware Q&A engine.
        Uses persona, structured analysis, and domain patterns.
        """

        # 1. Persona
        from app.agents.prompt_roles import get_role_for_doc_type
        role = get_role_for_doc_type(doc_type)

        # 2. Patterns (used as reasoning anchors)
        from app.agents.doc_type_prompts import (
            doc_type_insights_prompt,
            doc_type_summary_prompt,
            GENERIC_INSIGHTS
        )

        kp_patterns = doc_type_insights_prompt.get(doc_type, GENERIC_INSIGHTS)
        summary_patterns = doc_type_summary_prompt.get(doc_type, GENERIC_INSIGHTS)

        # 3. Build the Q&A prompt
        prompt = f"""
    {role}

    You are answering a question about a {doc_type} document.

    Below is the document context (raw text + structured analysis):

    ================ DOCUMENT CONTEXT ================
    {context}
    ==================================================

    DOMAIN REASONING GUIDANCE:
    - Key point patterns for this document type include:
      {kp_patterns[:5]}
    - Summary patterns for this document type include:
      {summary_patterns[:5]}

    Use these patterns ONLY as reasoning anchors, not as output templates.

    INSTRUCTIONS FOR ANSWERING:
    1. Use ONLY the information provided in the context above.
    2. If structured summary/key_points/insights exist, use them first.
    3. For numbers, dates, totals, and counts — use exact values from the document.
    4. If the answer is not in the document, say: "The document does not provide this information."
    5. Cite the document explicitly using short quotes.
    6. Be precise, factual, and avoid assumptions.
    7. Answer in clear, well-structured paragraphs.

    QUESTION:
    {question}

    Return ONLY the answer text. No JSON, no markdown.
    """

        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt
            )
            return response["response"].strip()

        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return f"Error generating answer: {str(e)}"



#     def _generate_answer(self, question: str, context: str) -> str:
#         """Generate answer using LLM with universal instructions"""
#
#         prompt = f"""{context}
#
# INSTRUCTIONS FOR ANSWERING:
# 1. Use ALL the information provided above (document content + structured analysis if available)
# 2. If structured statistics are provided, use them for counts and aggregations
# 3. For detailed lists, reference the actual data provided
# 4. Be precise with numbers - don't approximate or guess
# 5. If you don't know or data isn't available, say so clearly
# 6. Cite specific information from the document when relevant
#
# CURRENT QUESTION: {question}
#
# ANSWER (be detailed and accurate):
# """
#
#         try:
#             response = self.client.generate(
#                 model=self.model,
#                 prompt=prompt
#             )
#             return response['response'].strip()
#
#         except Exception as e:
#             logger.error(f"LLM generation failed: {e}")
#             return f"Error generating answer: {str(e)}"


# FastAPI Integration
class UniversalQAService:
    """
    Service for integrating Universal Q&A with FastAPI
    Works with ANY document type
    """

    def __init__(self, ollama_model: str = "mistral:latest"):
        self.qa_system = UniversalDocumentQA(ollama_model)

    def ask_question(
            self,
            question: str,
            document: Any,  # SQLAlchemy document object
            conversation_history: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Ask question about any document

        Works with:
        - Medical records
        - Bank statements
        - Sales reports
        - Legal documents
        - Technical docs
        - Any other document type
        """

        # Extract raw text (FULL, not truncated!)
        raw_text = document.raw_text or ""
        doc_metadata = document.doc_metadata
        # Get analysis results if available
        analysis_results = None
        if doc_metadata["analyzed_data"]:
            analysis_results = doc_metadata["analyzed_data"]

        # Build metadata
        metadata = {
            "filename": document.filename,
            "type": document.doc_type,
            "size": f"{len(raw_text)} characters",
        }

        # Get answer
        result = self.qa_system.ask(
            question=question,
            raw_text=raw_text,  # ← Full text!
            analysis_results=analysis_results,
            metadata=metadata,
            conversation_history=conversation_history
        )

        # Add document info
        result["document_id"] = str(document.id)
        result["document_name"] = document.filename

        return result
