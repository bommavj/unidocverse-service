"""
ai_service.py  (PydanticAI rewrite)
─────────────────────────────────────────────────────────────────────────────
Drop-in replacement for the original ai_service.py.

WHAT CHANGED AND WHY:
  Old code had the same ~40 line pattern in every function:
    1. Build a giant prompt begging the model for valid JSON
    2. Strip ```json fences manually
    3. json.loads() — crashes on trailing commas, extra text, etc.
    4. Manually validate each field type
    5. Return a plain dict with no type info

  With PydanticAI:
    1. Define a schema once (Pydantic BaseModel)
    2. PydanticAI injects the JSON schema into the prompt automatically
    3. If validation fails → it retries with the error, up to max_retries times
    4. You get back a typed Python object — IDE autocomplete, no KeyError surprises
    5. Zero JSON parsing code to maintain

  Same public interface as the original — callers don't change.

INSTALL:
  pip install pydantic-ai
"""

from __future__ import annotations

import logging
import os
from typing import List

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Schemas — define once, validated everywhere
# ─────────────────────────────────────────────────────────────────────────────

class Insight(BaseModel):
    title:       str = Field(description="Short insight title, 3-8 words")
    category:    str = Field(default="analysis",
                             description="One of: analysis, recommendation, observation, trend")
    description: str = Field(description="1-3 sentence explanation specific to this document")


class InsightList(BaseModel):
    insights: List[Insight] = Field(description="List of insights")


class KeyPointList(BaseModel):
    key_points: List[str] = Field(description="List of concise key points, 1-2 sentences each")


# ─────────────────────────────────────────────────────────────────────────────
# Ollama model factory — points PydanticAI at local Ollama
# ─────────────────────────────────────────────────────────────────────────────

def _make_model(model_name: str, host: str):
    """
    Create a PydanticAI OpenAI-compatible model pointing at local Ollama.
    Ollama exposes /v1/chat/completions on port 11434.
    """
    from pydantic_ai.models.openai import OpenAIModel
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url=f"{host.rstrip('/')}/v1",
        api_key="ollama",  # required field, ignored by Ollama
    )
    return OpenAIModel(model_name, openai_client=client)


# ─────────────────────────────────────────────────────────────────────────────
# OllamaService — same class name, same public methods, PydanticAI internals
# ─────────────────────────────────────────────────────────────────────────────

class OllamaService:
    def __init__(self, model: str = None, host: str = None):
        self.model = model or os.getenv("OLLAMA_MODEL", "phi3:mini")
        self.host  = host  or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        # lazy-created agents
        self._insight_agent   = None
        self._keypoint_agent  = None

        logger.info(f"🤖 [OllamaService] model={self.model}  host={self.host}")

    # ── agent factories ───────────────────────────────────────────────────────
    def _get_insight_agent(self):
        if self._insight_agent is None:
            from pydantic_ai import Agent
            self._insight_agent = Agent(
                model=_make_model(self.model, self.host),
                output_type=InsightList,
                instructions=(
                    "You are a document analysis assistant. "
                    "Generate specific, actionable insights about the provided document. "
                    "Each insight must reference actual content from the document."
                ),
            )
        return self._insight_agent

    def _get_keypoint_agent(self):
        if self._keypoint_agent is None:
            from pydantic_ai import Agent
            self._keypoint_agent = Agent(
                model=_make_model(self.model, self.host),
                output_type=KeyPointList,
                instructions=(
                    "You are a document analysis assistant. "
                    "Extract concise, factual key points from the provided document. "
                    "Each key point should be 1-2 sentences and reference specific facts."
                ),
            )
        return self._keypoint_agent

    # ── keep generate() for any callers that use the raw method ───────────────
    def generate(self, prompt: str) -> str:
        """Raw generation — kept for backward compatibility."""
        try:
            import ollama
            client   = ollama.Client(host=self.host)
            response = client.generate(model=self.model, prompt=prompt)
            return response["response"]
        except Exception as e:
            logger.error(f"❌ [OllamaService] raw generate failed: {e}")
            raise

    # ── insights ──────────────────────────────────────────────────────────────
    async def generate_additional_insights(
        self,
        document,
        existing_count: int,
        needed_count: int,
    ) -> List[dict]:
        """
        Generate additional insights for a document.
        Replaces the old 80-line JSON-wrangling version.
        Same return type: List[dict] with keys title, category, description.
        """
        try:
            text_content = self._extract_text(document)
            if not text_content:
                logger.warning("⚠️  [OllamaService] No text for insight generation")
                return []

            existing_titles: List[str] = []
            if document.doc_metadata:
                existing_titles = [
                    ins.get("title", "")
                    for ins in document.doc_metadata.get("insights", [])
                ]

            prompt = (
                f'Document: "{document.filename}" (type: {document.doc_type})\n\n'
                f"Content:\n{text_content[:6000]}\n\n"
                f"Already generated insights ({existing_count}):\n"
                + "\n".join(f"- {t}" for t in existing_titles)
                + f"\n\nGenerate {needed_count} NEW insights not yet covered."
            )

            agent  = self._get_insight_agent()
            result = await agent.run(prompt)

            insights = [i.model_dump() for i in result.output.insights[:needed_count]]
            logger.info(f"✅ [OllamaService] Generated {len(insights)} insights")
            return insights

        except Exception as e:
            logger.error(f"❌ [OllamaService] Insight generation failed: {e}", exc_info=True)
            return []

    # ── key points ────────────────────────────────────────────────────────────
    async def generate_additional_key_points(
        self,
        document,
        existing_count: int,
        needed_count: int,
    ) -> List[str]:
        """
        Generate additional key points for a document.
        Replaces the old 80-line JSON-wrangling version.
        Same return type: List[str].
        """
        try:
            text_content = self._extract_text(document)
            if not text_content:
                logger.warning("⚠️  [OllamaService] No text for key point generation")
                return []

            existing_points: List[str] = []
            if document.doc_metadata:
                existing_points = document.doc_metadata.get("key_points", [])

            prompt = (
                f'Document: "{document.filename}" (type: {document.doc_type})\n\n'
                f"Content:\n{text_content[:6000]}\n\n"
                f"Existing key points ({existing_count}):\n"
                + "\n".join(f"- {p}" for p in existing_points)
                + f"\n\nGenerate {needed_count} NEW key points not yet covered."
            )

            agent  = self._get_keypoint_agent()
            result = await agent.run(prompt)

            points = result.output.key_points[:needed_count]
            logger.info(f"✅ [OllamaService] Generated {len(points)} key points")
            return points

        except Exception as e:
            logger.error(f"❌ [OllamaService] Key point generation failed: {e}", exc_info=True)
            return []

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _extract_text(document) -> str:
        if document.doc_metadata and "extracted_text" in document.doc_metadata:
            return document.doc_metadata["extracted_text"]
        if document.summary:
            return document.summary
        return ""


# ── module-level singleton — same as before ───────────────────────────────────
ollama_service = OllamaService()


# ── standalone functions — same as before ────────────────────────────────────
async def generate_additional_insights(document, existing_count: int, needed_count: int) -> List[dict]:
    return await ollama_service.generate_additional_insights(document, existing_count, needed_count)


async def generate_additional_key_points(document, existing_count: int, needed_count: int) -> List[str]:
    return await ollama_service.generate_additional_key_points(document, existing_count, needed_count)


# import logging
# from typing import List
# import ollama
# import json
# import os
#
# from app.core import config
#
# logger = logging.getLogger(__name__)
#
#
# class OllamaService:
#
#     # self.model = model or os.getenv("OLLAMA_MODEL", "mistral:7b")
#     def __init__(self, model: str = None, host: str = None):
#         self.model = model or os.getenv("OLLAMA_MODEL", config.model)
#         self.host = host or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
#         self.client = ollama.Client(host=self.host)
#         logger.info(f"🤖 Initialized Ollama service with model: {self.model}, host: {self.host}")
#
#     def generate(self, prompt: str) -> str:
#         """Generate response from Ollama"""
#         try:
#             response = self.client.generate(
#                 model=self.model,
#                 prompt=prompt
#             )
#             return response['response']
#         except Exception as e:
#             logger.error(f"❌ Ollama generation error: {e}")
#             raise
#
#     async def generate_additional_insights(self, document, existing_count: int, needed_count: int) -> List[dict]:
#         """
#         Generate additional AI insights for a document using Ollama
#         """
#         try:
#             logger.info(f"🤖 Generating {needed_count} additional insights for {document.filename}")
#
#             # ✅ Get text content from metadata or summary
#             text_content = ""
#             if document.doc_metadata and "extracted_text" in document.doc_metadata:
#                 text_content = document.doc_metadata["extracted_text"]
#             elif document.summary:
#                 text_content = document.summary
#
#             if not text_content:
#                 logger.warning("⚠️ No text content available for generating insights")
#                 return []
#
#             # Limit text to avoid token limits
#             text_content = text_content[:6000]
#
#             # Get existing insights to avoid duplicates
#             existing_insights = document.doc_metadata.get("insights", []) if document.doc_metadata else []
#             existing_titles = [ins.get("title", "") for ins in existing_insights]
#
#             prompt = f"""You are analyzing a document titled "{document.filename}" of type "{document.doc_type}".
#
# Document content:
# {text_content}
#
# Already covered insights ({existing_count} total):
# {json.dumps(existing_titles, indent=2)}
#
# Task: Generate {needed_count} NEW and DIFFERENT insights that haven't been covered yet.
#
# Requirements:
# 1. Each insight must be unique and actionable
# 2. Include a clear title and category (analysis/recommendation/observation/trend)
# 3. Must be completely different from existing insights
# 4. Be specific to this document
#
# CRITICAL: Return ONLY a valid JSON array. No markdown, no code blocks, no explanations.
# Format:
# [
#   {{"title": "Insight Title", "category": "analysis", "description": "Detailed description"}},
#   {{"title": "Another Title", "category": "recommendation", "description": "Another description"}}
# ]
#
# Generate exactly {needed_count} insights:"""
#
#             # Call Ollama
#             response_text = self.generate(prompt)
#
#             logger.info(f"📝 Raw Ollama response length: {len(response_text)}")
#             logger.debug(f"Response preview: {response_text[:300]}...")
#
#             # Clean response
#             content = response_text.strip()
#
#             # Extract JSON from markdown code blocks if present
#             if "```json" in content:
#                 content = content.split("```json")[1].split("```")[0].strip()
#             elif "```" in content:
#                 content = content.split("```")[1].split("```")[0].strip()
#
#             # Try to find JSON array in the content
#             if not content.startswith('['):
#                 start_idx = content.find('[')
#                 end_idx = content.rfind(']')
#                 if start_idx != -1 and end_idx != -1:
#                     content = content[start_idx:end_idx + 1]
#                 else:
#                     logger.error(f"❌ No JSON array found in response: {content[:200]}")
#                     return []
#
#             # Parse JSON
#             insights = json.loads(content)
#
#             # Ensure it's a list
#             if not isinstance(insights, list):
#                 insights = [insights]
#
#             # Validate and clean insights
#             valid_insights = []
#             for insight in insights:
#                 if isinstance(insight, dict) and "title" in insight and "description" in insight:
#                     if "category" not in insight:
#                         insight["category"] = "analysis"
#                     valid_insights.append(insight)
#
#             logger.info(f"✅ Successfully generated {len(valid_insights)} new insights")
#             return valid_insights
#
#         except json.JSONDecodeError as e:
#             logger.error(f"❌ JSON parsing error: {e}")
#             logger.error(f"Response was: {content[:500] if 'content' in locals() else 'N/A'}")
#             return []
#         except Exception as e:
#             logger.error(f"❌ Error generating insights: {e}")
#             import traceback
#             traceback.print_exc()
#             return []
#
#     async def generate_additional_key_points(self, document, existing_count: int, needed_count: int) -> List[str]:
#         """
#         Generate additional key points for a document using Ollama
#         """
#         try:
#             logger.info(f"🤖 Generating {needed_count} additional key points for {document.filename}")
#
#             # ✅ Get text content from metadata or summary
#             text_content = ""
#             if document.doc_metadata and "extracted_text" in document.doc_metadata:
#                 text_content = document.doc_metadata["extracted_text"]
#             elif document.summary:
#                 text_content = document.summary
#
#             if not text_content:
#                 logger.warning("⚠️ No text content available for generating key points")
#                 return []
#
#             # Limit text to avoid token limits
#             text_content = text_content[:6000]
#
#             # Get existing key points to avoid duplicates
#             existing_key_points = document.doc_metadata.get("key_points", []) if document.doc_metadata else []
#
#             prompt = f"""You are analyzing a document titled "{document.filename}" of type "{document.doc_type}".
#
# Document content:
# {text_content}
#
# Already covered key points ({existing_count} total):
# {json.dumps(existing_key_points, indent=2)}
#
# Task: Generate {needed_count} NEW and DIFFERENT key points that haven't been covered yet.
#
# Requirements:
# 1. Each key point must be concise (1-2 sentences maximum)
# 2. Highlight important information from the document
# 3. Must be completely different from existing key points
# 4. Be specific and factual
#
# CRITICAL: Return ONLY a valid JSON array of strings. No markdown, no code blocks, no explanations.
# Format:
# ["Key point 1 here", "Key point 2 here", "Key point 3 here"]
#
# Generate exactly {needed_count} key points:"""
#
#             # Call Ollama
#             response_text = self.generate(prompt)
#
#             logger.info(f"📝 Raw Ollama response length: {len(response_text)}")
#             logger.debug(f"Response preview: {response_text[:300]}...")
#
#             # Clean response
#             content = response_text.strip()
#
#             # Extract JSON from markdown code blocks if present
#             if "```json" in content:
#                 content = content.split("```json")[1].split("```")[0].strip()
#             elif "```" in content:
#                 content = content.split("```")[1].split("```")[0].strip()
#
#             # Try to find JSON array in the content
#             if not content.startswith('['):
#                 start_idx = content.find('[')
#                 end_idx = content.rfind(']')
#                 if start_idx != -1 and end_idx != -1:
#                     content = content[start_idx:end_idx + 1]
#                 else:
#                     logger.error(f"❌ No JSON array found in response: {content[:200]}")
#                     return []
#
#             # Parse JSON
#             key_points = json.loads(content)
#
#             # Ensure it's a list of strings
#             if not isinstance(key_points, list):
#                 key_points = [str(key_points)]
#
#             # Convert all items to strings and clean
#             key_points = [str(point).strip() for point in key_points if point]
#
#             logger.info(f"✅ Successfully generated {len(key_points)} new key points")
#             return key_points
#
#         except json.JSONDecodeError as e:
#             logger.error(f"❌ JSON parsing error: {e}")
#             logger.error(f"Response was: {content[:500] if 'content' in locals() else 'N/A'}")
#             return []
#         except Exception as e:
#             logger.error(f"❌ Error generating key points: {e}")
#             import traceback
#             traceback.print_exc()
#             return []
#
#
# # ✅ Create a global instance
# ollama_service = OllamaService()
#
#
# # ✅ Export standalone functions that use the service
# async def generate_additional_insights(document, existing_count: int, needed_count: int) -> List[dict]:
#     """Wrapper function for backward compatibility"""
#     return await ollama_service.generate_additional_insights(document, existing_count, needed_count)
#
#
# async def generate_additional_key_points(document, existing_count: int, needed_count: int) -> List[str]:
#     """Wrapper function for backward compatibility"""
#     return await ollama_service.generate_additional_key_points(document, existing_count, needed_count)