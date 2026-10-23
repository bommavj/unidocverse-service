"""First-launch setup progress (Starter edition: the search model download)."""
from fastapi import APIRouter, Depends

from app.api.auth_router import get_current_user
from app.services import model_fetch

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/models")
def models_status(_: dict = Depends(get_current_user)):
    return {"reranker": model_fetch.status()}
