# routes/model_routes.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.model_manager import get_model_registry
from app.core import config
from app.config.model_manager import get_model_registry

router = APIRouter(prefix="/api/models", tags=["models"])

import logging
logger = logging.getLogger(__name__)

class ModelRequest(BaseModel):
    model_name: str


@router.post("/set-model")
def set_model(req: ModelRequest):
    requested = req.model_name
    from app.config.model_manager import is_model_available
    installed = is_model_available(requested)
    selected = config.set_model(requested)
    model_info = {
        "success": True,
        "selected_model": selected,
        "installed": installed,
        "message": f"Model '{selected}' selected successfully." if installed else f"Model '{selected}' selected but not yet installed in Ollama — run: ollama pull {selected}",
    }
    logger.info(f"{model_info}")
    return model_info


@router.get("/get-model")
def get_model():
    return {
        "selected_model": config.model,
        "message": f"Current active model is '{config.model}'."
    }


@router.get("/list-models")
def list_models():
    registry = get_model_registry()

    return {
        "installed_models": list(registry.keys()),
        "count": len(registry),
        "message": "Installed models discovered successfully."
    }
