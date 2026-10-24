"""First-launch setup progress (Starter edition: the search model download)."""
import os

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_router import get_current_user, require_admin
from app.services import model_fetch

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/models")
def models_status(_: dict = Depends(get_current_user)):
    return {"reranker": model_fetch.RERANKER.state(), "answer": model_fetch.ANSWER.state(),
            "answer_model": os.getenv("ANSWER_MODEL_NAME", "phi3:mini")}


@router.post("/models/answer")
def download_answer_model(_=Depends(require_admin)):
    """'Built-in model' on Home: fetch the answer model (about 2 GB) into the app's data folder."""
    if not model_fetch.ANSWER.enabled():
        raise HTTPException(400, "This installation has no built-in model download. Install Ollama or add a cloud key.")
    model_fetch.ANSWER.start()
    return model_fetch.ANSWER.state()
