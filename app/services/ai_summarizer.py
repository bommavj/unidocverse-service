"""
ai_summarizer.py  (PydanticAI rewrite)
─────────────────────────────────────────────────────────────────────────────
Drop-in replacement for the original ai_summarizer.py.

WHAT CHANGED:
  summarize()          — validated SummaryResult, .summary guaranteed str
  extract_key_points() — validated KeyPointsResult, guaranteed List[str]
  is_available()       — unchanged

INSTALL:
  pip install pydantic-ai
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class SummaryResult(BaseModel):
    summary: str = Field(
        description="Concise summary focusing on key facts, numbers, dates, and main points"
    )


class KeyPointsResult(BaseModel):
    key_points: List[str] = Field(
        description="List of the most important key points, each 1-2 sentences"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AISummarizer
# ─────────────────────────────────────────────────────────────────────────────

class AISummarizer:
    """
    AI-powered document summarization using PydanticAI + Ollama.
    Same interface as the original — drop-in replacement.
    """

    def __init__(self, model: str = "phi3:mini", host: str = "http://localhost:11434"):
        self.model = model
        self.host  = host
        self._summary_agent   = None
        self._keypoints_agent = None
        self._ollama_client   = None

    def _make_pydantic_model(self):
        from pydantic_ai.models.openai import OpenAIModel
        from openai import AsyncOpenAI
        client = AsyncOpenAI(base_url=f"{self.host.rstrip('/')}/v1", api_key="ollama")
        return OpenAIModel(self.model, openai_client=client)

    def _get_summary_agent(self):
        if self._summary_agent is None:
            from pydantic_ai import Agent
            self._summary_agent = Agent(
                model=self._make_pydantic_model(),
                output_type=SummaryResult,
                instructions=(
                    "You are a document summarization assistant. "
                    "Produce concise, factual summaries. "
                    "Always mention specific numbers, dates, and named entities when present."
                ),
            )
        return self._summary_agent

    def _get_keypoints_agent(self):
        if self._keypoints_agent is None:
            from pydantic_ai import Agent
            self._keypoints_agent = Agent(
                model=self._make_pydantic_model(),
                output_type=KeyPointsResult,
                instructions=(
                    "You are a document analysis assistant. "
                    "Extract the most important factual key points. "
                    "Each key point must reference specific content from the document."
                ),
            )
        return self._keypoints_agent

    def summarize(self, text: str, max_length: int = 200, doc_type: Optional[str] = None) -> Optional[str]:
        """Generate summary. Returns str or None on failure. Same signature as original."""
        try:
            if len(text) > 15000:
                text = text[:15000] + "..."
            doc_hint = f"This is a {doc_type}. " if doc_type else ""
            prompt = (
                f"{doc_hint}Summarize the following document in {max_length} words or less. "
                f"Focus on key facts, numbers, dates, and main points.\n\nDocument:\n{text}"
            )
            result  = self._run_async(self._get_summary_agent().run(prompt))
            summary = result.output.summary.strip()
            logger.info(f"✅ [AISummarizer] Summary: {len(summary)} chars")
            return summary
        except Exception as e:
            logger.error(f"❌ [AISummarizer] summarize() failed: {e}", exc_info=True)
            return None

    def extract_key_points(self, text: str, count: int = 5) -> Optional[List[str]]:
        """Extract key points. Returns List[str] or None on failure. Same signature as original."""
        try:
            if len(text) > 15000:
                text = text[:15000] + "..."
            prompt = (
                f"Extract the {count} most important key points from this document. "
                f"Each must be 1-2 sentences and specific to the content.\n\nDocument:\n{text}"
            )
            result = self._run_async(self._get_keypoints_agent().run(prompt))
            points = result.output.key_points[:count]
            logger.info(f"✅ [AISummarizer] Extracted {len(points)} key points")
            return points
        except Exception as e:
            logger.error(f"❌ [AISummarizer] extract_key_points() failed: {e}", exc_info=True)
            return None

    def is_available(self) -> bool:
        """Check if Ollama is reachable. Unchanged from original."""
        try:
            import ollama
            if self._ollama_client is None:
                self._ollama_client = ollama.Client(host=self.host)
            self._ollama_client.list()
            return True
        except Exception:
            return False

    @staticmethod
    def _run_async(coro):
        """Run a coroutine safely from sync code — handles FastAPI event loop context."""
        try:
            asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result(timeout=120)
        except RuntimeError:
            return asyncio.run(coro)


# import json
# import ollama
# from typing import Optional
# import logging
#
# logger = logging.getLogger(__name__)
#
#
# class AISummarizer:
#     """AI-powered document summarization using Ollama — single call, fast."""
#
#     def __init__(self, model: str = "phi3:mini", host: str = "http://localhost:11434"):
#         self.model = model
#         self.host = host
#         self.client = ollama.Client(host=host)
#
#     def analyze(
#             self,
#             text: str,
#             max_length: int = 200,
#             key_points_count: int = 5,
#             doc_type: Optional[str] = None
#     ) -> dict:
#         """
#         Single call to phi3:mini — returns summary + key points + insights together.
#         Replaces 3 separate calls (33 sec) with 1 call (10-12 sec).
#
#         Returns:
#             {
#                 "summary":    "...",
#                 "key_points": ["...", "..."],
#                 "insights":   [{"title": "...", "category": "...", "description": "..."}]
#             }
#         """
#         try:
#             # Truncate to fit phi3:mini context window efficiently
#             if len(text) > 8000:
#                 text = text[:8000] + "..."
#
#             doc_context = f"a {doc_type}" if doc_type else "a document"
#
#             prompt = f"""Analyze {doc_context} and return JSON only. No explanation, no markdown.
#
# Document:
# {text}
#
# Return this exact JSON structure:
# {{
#   "summary": "concise summary in {max_length} words max, focus on key facts and numbers",
#   "key_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
#   "insights": [
#     {{"title": "insight title", "category": "analysis", "description": "detailed insight"}},
#     {{"title": "insight title", "category": "recommendation", "description": "detailed insight"}},
#     {{"title": "insight title", "category": "observation", "description": "detailed insight"}}
#   ]
# }}"""
#
#             response = self.client.generate(
#                 model=self.model,
#                 prompt=prompt,
#                 options={
#                     "temperature": 0.1,      # low = faster, more consistent
#                     "num_predict": 1024,     # cap output length
#                     "num_ctx":     4096,     # smaller context = faster
#                 }
#             )
#
#             content = response["response"].strip()
#
#             # Strip markdown fences if present
#             if "```json" in content:
#                 content = content.split("```json")[1].split("```")[0].strip()
#             elif "```" in content:
#                 content = content.split("```")[1].split("```")[0].strip()
#
#             # Find JSON object
#             start = content.find("{")
#             end   = content.rfind("}") + 1
#             if start != -1 and end > start:
#                 content = content[start:end]
#
#             result = json.loads(content)
#
#             # Validate and normalise
#             summary    = str(result.get("summary", "")).strip()
#             key_points = result.get("key_points", [])
#             insights   = result.get("insights", [])
#
#             if not isinstance(key_points, list):
#                 key_points = []
#             if not isinstance(insights, list):
#                 insights = []
#
#             key_points = [str(p).strip() for p in key_points if p][:key_points_count]
#             insights   = [i for i in insights if isinstance(i, dict) and "title" in i][:5]
#
#             logger.info(
#                 f"✅ analyze() done — "
#                 f"summary={len(summary)}c, "
#                 f"key_points={len(key_points)}, "
#                 f"insights={len(insights)}"
#             )
#
#             return {
#                 "summary":    summary,
#                 "key_points": key_points,
#                 "insights":   insights,
#             }
#
#         except json.JSONDecodeError as e:
#             logger.error(f"JSON parse error: {e} — falling back to text mode")
#             return self._fallback_analyze(text, max_length, key_points_count, doc_type)
#
#         except Exception as e:
#             logger.error(f"analyze() failed: {e}")
#             return {"summary": None, "key_points": [], "insights": []}
#
#     # ── Backward-compatible wrappers ─────────────────────────────────────────
#
#     def summarize(
#             self,
#             text: str,
#             max_length: int = 200,
#             doc_type: Optional[str] = None
#     ) -> Optional[str]:
#         """
#         Backward compatible — calls analyze() internally.
#         Callers that only need summary still work unchanged.
#         """
#         result = self.analyze(text, max_length=max_length, doc_type=doc_type)
#         return result.get("summary")
#
#     def extract_key_points(self, text: str, count: int = 5) -> Optional[list]:
#         """
#         Backward compatible — calls analyze() internally.
#         Callers that only need key points still work unchanged.
#         """
#         result = self.analyze(text, key_points_count=count)
#         return result.get("key_points") or []
#
#     def is_available(self) -> bool:
#         """Check if Ollama service is available."""
#         try:
#             self.client.list()
#             return True
#         except Exception:
#             return False
#
#     # ── Fallback — plain text parsing if JSON fails ──────────────────────────
#
#     def _fallback_analyze(
#             self,
#             text: str,
#             max_length: int,
#             key_points_count: int,
#             doc_type: Optional[str]
#     ) -> dict:
#         """Plain text fallback if phi3:mini returns non-JSON."""
#         logger.info("Using fallback plain-text analysis")
#         try:
#             if len(text) > 8000:
#                 text = text[:8000] + "..."
#
#             doc_context = f"a {doc_type}" if doc_type else "a document"
#
#             prompt = f"""Analyze {doc_context}.
#
# Document:
# {text}
#
# Write:
# SUMMARY: (one paragraph, max {max_length} words)
# KEY POINTS:
# 1.
# 2.
# 3.
# 4.
# 5."""
#
#             response = self.client.generate(
#                 model=self.model,
#                 prompt=prompt,
#                 options={"temperature": 0.1, "num_predict": 800, "num_ctx": 4096}
#             )
#
#             content  = response["response"].strip()
#             summary  = ""
#             points   = []
#
#             for line in content.split("\n"):
#                 line = line.strip()
#                 if line.startswith("SUMMARY:"):
#                     summary = line.replace("SUMMARY:", "").strip()
#                 elif line and line[0].isdigit() and "." in line[:3]:
#                     point = line.split(".", 1)[-1].strip()
#                     if point:
#                         points.append(point)
#
#             if not summary:
#                 summary = content[:500]
#
#             return {
#                 "summary":    summary,
#                 "key_points": points[:key_points_count],
#                 "insights":   [],
#             }
#
#         except Exception as e:
#             logger.error(f"Fallback also failed: {e}")
#             return {"summary": None, "key_points": [], "insights": []}