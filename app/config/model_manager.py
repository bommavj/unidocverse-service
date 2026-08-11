import os
import requests

_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")


def discover_ollama_models() -> list[str]:
    try:
        resp = requests.get(f"{_OLLAMA_HOST}/api/tags", timeout=2)
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def discover_local_models(path="/models") -> list[str]:
    if not os.path.exists(path):
        return []
    return [f for f in os.listdir(path) if f.endswith(".gguf") or f.endswith(".bin")]


def get_model_registry() -> dict:
    """
    Always queries Ollama fresh — no cache.
    This way newly pulled models are visible immediately without restart.
    """
    registry = {}
    for name in discover_ollama_models():
        registry[name] = {"provider": "ollama", "name": name, "context": 4096}
    for filename in discover_local_models():
        registry[filename] = {"provider": "local", "path": f"/models/{filename}", "context": 2048}
    return registry


def is_model_available(name: str) -> bool:
    """Check if a model is currently available in Ollama (live check)."""
    installed = discover_ollama_models()
    return name in installed
