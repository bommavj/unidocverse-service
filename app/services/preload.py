"""
Preload Manager for UniDocVerse Backend
Preloads Ollama model and embedding model at startup for instant API responses
"""

import asyncio
import logging
import os
import time

import httpx

from app.core import config

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # Will be handled in preload_embeddings

log = logging.getLogger(__name__)

# Configuration
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = config.model


# Auto-detect embedding model path
def get_embedding_model_path():
    """
    Intelligently find the embedding model path.
    Checks multiple locations in order:
    1. MODEL_PATH environment variable
    2. MODELS_PATH/all-mpnet-base-v2
    3. Relative to current file: ../../models/all-mpnet-base-v2
    4. Fallback: models/all-mpnet-base-v2
    """

    # Try MODEL_PATH env var
    model_path = os.getenv("MODEL_PATH")
    if model_path and os.path.exists(model_path):
        return model_path

    # Try MODELS_PATH/all-mpnet-base-v2
    models_path = os.getenv("MODELS_PATH")
    if models_path:
        full_path = os.path.join(models_path, config.embedding)
        if os.path.exists(full_path):
            return full_path

    # Try relative to this file (app/services/preload.py)
    # Go up to project root and find models/
    current_file = os.path.abspath(__file__)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
    relative_path = os.path.join(project_root, "models", config.embedding)
    if os.path.exists(relative_path):
        return relative_path

    # Fallback to relative path (might work if running from project root)
    return f"models/{config.embedding}"


EMBEDDING_MODEL_PATH = get_embedding_model_path()


class PreloadManager:
    """Manages preloading of models at startup"""

    def __init__(self):
        self.ollama_loaded = False
        self.embeddings_loaded = False
        self.sentence_transformer = None

    async def preload_ollama(self):
        """
        Preload Ollama model into VRAM/RAM.
        First API call loads model (~5-30s), this does it at startup.
        """

        if not OLLAMA_BASE_URL:
            log.info("⊘ Ollama not configured - skipping preload")
            return

        try:
            log.info(f"🔄 Preloading Ollama model: {OLLAMA_MODEL}")
            start_time = time.time()

            async with httpx.AsyncClient(timeout=120.0) as client:
                # 1. Check if Ollama is running
                try:
                    health = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
                    health.raise_for_status()
                except Exception as e:
                    log.warning(f"⚠️  Ollama not available: {e}")
                    return

                # 2. Verify model exists
                tags_data = health.json()
                model_exists = any(
                    m.get("name") == OLLAMA_MODEL or m.get("name") == "mistral:latest"
                    for m in tags_data.get("models", [])
                )

                if not model_exists:
                    log.error(f"❌ Model '{OLLAMA_MODEL}' not found in Ollama")
                    log.error(f"   💡 Run: ollama pull {OLLAMA_MODEL}")
                    return

                # 3. Send minimal prompt to load model into memory
                log.info("   ⏳ Loading model into VRAM/RAM...")
                preload_request = {
                    "model": OLLAMA_MODEL,
                    "prompt": "test",
                    "stream": False,
                    "options": {
                        "num_predict": 1,  # Only generate 1 token
                        "temperature": 0,
                        "num_ctx": 2048
                    }
                }

                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json=preload_request,
                    timeout=120.0
                )
                response.raise_for_status()

                elapsed = time.time() - start_time
                self.ollama_loaded = True

                log.info(f"✅ Ollama preloaded in {elapsed:.1f}s")
                log.info(f"   🚀 AI classification & summaries will be instant!")

        except httpx.HTTPStatusError as e:
            log.error(f"❌ Ollama HTTP error {e.response.status_code}: {e}")
        except httpx.TimeoutException:
            log.error(f"❌ Ollama preload timeout (>120s) - model may be too large")
        except Exception as e:
            log.error(f"❌ Ollama preload failed: {type(e).__name__}: {e}")

    async def preload_embeddings(self):
        """
        Preload SentenceTransformer embedding model.
        First call loads model (~2-5s), this does it at startup.
        """

        try:
            log.info(f"🔄 Preloading embedding model: {EMBEDDING_MODEL_PATH}")

            # Check if path exists
            if not os.path.exists(EMBEDDING_MODEL_PATH):
                log.error(f"❌ Embedding model path does not exist: {EMBEDDING_MODEL_PATH}")
                log.error(f"   💡 Check MODEL_PATH or MODELS_PATH environment variable")
                log.error(f"   💡 Current MODEL_PATH: {os.getenv('MODEL_PATH')}")
                log.error(f"   💡 Current MODELS_PATH: {os.getenv('MODELS_PATH')}")
                return

            start_time = time.time()

            # Import here if not imported globally
            if SentenceTransformer is None:
                from sentence_transformers import SentenceTransformer as ST
            else:
                ST = SentenceTransformer

            # Load model
            log.info(f"   ⏳ Loading model from: {EMBEDDING_MODEL_PATH}")
            from app.core.model_lock import MODEL_LOAD_LOCK
            with MODEL_LOAD_LOCK:
                self.sentence_transformer = ST(EMBEDDING_MODEL_PATH)

            # Warm up with a test encoding
            _ = self.sentence_transformer.encode("test", show_progress_bar=False)

            elapsed = time.time() - start_time
            self.embeddings_loaded = True

            log.info(f"✅ Embeddings preloaded in {elapsed:.1f}s")
            log.info(f"   🚀 Vector search will be instant!")

        except ImportError as e:
            log.error(f"❌ sentence-transformers not installed: {e}")
            log.error(f"   💡 Run: pip install sentence-transformers")
        except Exception as e:
            log.error(f"❌ Embedding preload failed: {type(e).__name__}: {e}")

    async def preload_all(self):
        """Preload all models in parallel"""

        log.info("📦 Starting model preload...")
        start_time = time.time()

        # Run both preloads concurrently
        await asyncio.gather(
            self.preload_ollama(),
            self.preload_embeddings(),
            return_exceptions=True  # Don't fail if one fails
        )

        elapsed = time.time() - start_time

        # Summary
        log.info("=" * 60)
        log.info(f"📊 Preload Summary ({elapsed:.1f}s total)")
        log.info(f"   Ollama (AI):     {'✅ Ready' if self.ollama_loaded else '❌ Failed'}")
        log.info(f"   Embeddings:      {'✅ Ready' if self.embeddings_loaded else '❌ Failed'}")
        log.info("=" * 60)

    def get_sentence_transformer(self):
        """Get preloaded SentenceTransformer instance"""
        return self.sentence_transformer


# Global instance
preload_manager = PreloadManager()

# ============================================================================
# Usage in your services
# ============================================================================

# Example: Use preloaded SentenceTransformer
from app.services.preload import preload_manager


class VectorService:
    def __init__(self):
        # Use preloaded model if available, otherwise load fresh
        self.model = preload_manager.get_sentence_transformer()

        if self.model is None:
            # Fallback: load model if preload failed
            from sentence_transformers import SentenceTransformer
            from app.core.model_lock import MODEL_LOAD_LOCK
            with MODEL_LOAD_LOCK:
                self.model = SentenceTransformer(EMBEDDING_MODEL_PATH)

    def encode(self, text: str):
        return self.model.encode(text)


# Example: Ollama usage remains the same
# The preload just makes it faster - no code changes needed!
import ollama


def classify_document(text: str):
    # This will be instant because model is preloaded
    # response = ollama.chat(
    #     model="phi3:mini",
    #     messages=[{"role": "user", "content": text}]
    # )
    response = ollama.chat(
        model="phi3:mini",
        messages=[{"role": "user", "content": text}]
    )
    return response
