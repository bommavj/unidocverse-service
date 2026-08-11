import io
import wave
import logging
from pathlib import Path
from functools import lru_cache
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tts", tags=["tts"])

MODELS_DIR = Path(__file__).parent.parent.parent / "models" / "piper"
MODEL_PATH = MODELS_DIR / "en_US-ryan-high.onnx"


@lru_cache(maxsize=1)
def _load_voice():
    from piper.voice import PiperVoice
    logger.info("Loading Piper TTS voice: en_US-ryan-high")
    return PiperVoice.load(str(MODEL_PATH))


class TTSRequest(BaseModel):
    text: str


@router.post("/speak", response_class=Response)
def speak(body: TTSRequest):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 4000:
        text = text[:4000]

    if not MODEL_PATH.exists():
        raise HTTPException(status_code=503, detail="TTS model not found")

    try:
        voice = _load_voice()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            voice.synthesize_wav(text, wf)
        buf.seek(0)
        return Response(content=buf.read(), media_type="audio/wav")
    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        raise HTTPException(status_code=500, detail="TTS synthesis failed")
