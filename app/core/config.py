import configparser
import logging
import platform
import os
from pydantic_settings import BaseSettings
from pathlib import Path
from app.config.model_manager import get_model_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Active model — single source of truth
# Priority: DB settings table > .env > hardcoded default
# ---------------------------------------------------------
model     = "phi3:mini"   # overwritten at startup by _load_active_model()
embedding = "all-mpnet-base-v2"
# One context size for every research-path LLM call: a different num_ctx makes
# Ollama reload the model (~3s). 4096 cut whole tables out of the prompt.
NUM_CTX   = 8192

CURRENT_MODEL = {"name": model}


def _load_active_model() -> str:
    """
    Load the active model from the DB settings table.
    Falls back to .env OLLAMA_MODEL, then 'phi3:mini'.
    Called once at startup so the last FE-selected model persists across restarts.
    """
    global model, CURRENT_MODEL
    try:
        from sqlalchemy import create_engine, text
        db_url = (
            f"postgresql://"
            f"{os.getenv('DB_USER', 'unidocverse')}:"
            f"{os.getenv('DB_PASSWORD', 'unidocverse')}@"
            f"{os.getenv('DB_HOST', 'localhost')}:"
            f"{os.getenv('DB_PORT', '54321')}/"
            f"{os.getenv('DB_NAME', 'unidocverse_db')}"
        )
        engine = create_engine(db_url)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM settings WHERE key = 'active_model' LIMIT 1")
            ).fetchone()
            if row:
                model = row[0]
                CURRENT_MODEL["name"] = model
                logger.info(f"✅ Active model loaded from DB: {model}")
                return model
    except Exception as e:
        logger.warning(f"⚠️ Could not load model from DB: {e}")

    # Fallback: .env → hardware auto-select
    env_model = os.getenv("OLLAMA_MODEL", "")
    if env_model:
        model = env_model
        CURRENT_MODEL["name"] = model
        logger.info(f"✅ Active model loaded from .env: {model}")
        return model

    # First launch — no DB setting, no .env override → pick best model for this hardware
    hw_model = _select_model_for_hardware()
    model = hw_model
    CURRENT_MODEL["name"] = model
    logger.info(f"✅ Active model auto-selected for hardware: {model}")
    return model


def _select_model_for_hardware() -> str:
    """
    Detect available RAM and return the best Ollama model that fits.
    Only called on first launch (no DB setting exists yet).

    Tiers (total RAM, not available — models are loaded on demand):
      >=32 GB → mistral:7b   (best quality for 7B class)
      >=16 GB → mistral:7b   (still fits with swap headroom)
       >=8 GB → phi3:mini    (3.8B, fits in 8 GB with OS overhead)
      < 8 GB  → phi3:mini    (smallest usable model)
    """
    try:
        import psutil
        total_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        total_gb = 0

    # Check which models are actually installed via Ollama
    installed: set[str] = set()
    try:
        from app.config.model_manager import discover_ollama_models
        installed = {m.split(":")[0] for m in discover_ollama_models()}
    except Exception:
        pass

    if total_gb >= 16 and "mistral" in installed:
        selected = "mistral:7b"
    elif total_gb >= 8 and "phi3" in installed:
        selected = "phi3:mini"
    elif installed:
        selected = next(iter(installed)) + ":latest"
    else:
        selected = "phi3:mini"  # default if Ollama not reachable yet

    logger.info(f"🖥️  Hardware detection: {total_gb:.1f} GB RAM → selected model: {selected}")
    return selected


def get_active_model() -> str:
    """Read the current active model from DB (live, reflects frontend changes)."""
    try:
        from sqlalchemy import create_engine, text as sa_text
        db_url = (
            f"postgresql://"
            f"{os.getenv('DB_USER', 'unidocverse')}:"
            f"{os.getenv('DB_PASSWORD', 'unidocverse')}@"
            f"{os.getenv('DB_HOST', 'localhost')}:"
            f"{os.getenv('DB_PORT', '54321')}/"
            f"{os.getenv('DB_NAME', 'unidocverse_db')}"
        )
        engine = create_engine(db_url)
        with engine.connect() as conn:
            row = conn.execute(
                sa_text("SELECT value FROM settings WHERE key = 'active_model' LIMIT 1")
            ).fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return CURRENT_MODEL.get("name") or model


def _persist_active_model(name: str) -> None:
    """
    Save the selected model to the DB settings table so it
    survives server restarts.
    """
    try:
        from sqlalchemy import create_engine, text
        db_url = (
            f"postgresql://"
            f"{os.getenv('DB_USER', 'unidocverse')}:"
            f"{os.getenv('DB_PASSWORD', 'unidocverse')}@"
            f"{os.getenv('DB_HOST', 'localhost')}:"
            f"{os.getenv('DB_PORT', '54321')}/"
            f"{os.getenv('DB_NAME', 'unidocverse_db')}"
        )
        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO settings (key, value)
                    VALUES ('active_model', :val)
                    ON CONFLICT (key) DO UPDATE SET value = :val
                """),
                {"val": name}
            )
            conn.commit()
        logger.info(f"✅ Active model persisted to DB: {name}")
    except Exception as e:
        logger.warning(f"⚠️ Could not persist model to DB: {e}")


def set_model(name: str) -> str:
    """
    Switch the active model. Updates in-memory global AND persists to DB.
    Called by the FE model switch API — survives restarts.
    Accepts any model name the user chooses — does a live Ollama check
    so newly pulled models work without restarting the server.
    """
    global model, CURRENT_MODEL
    from app.config.model_manager import is_model_available
    if is_model_available(name):
        model = name
        CURRENT_MODEL["name"] = name
        _persist_active_model(name)
        logger.info(f"✅ Model switched to: {name}")
        return model

    # Model name not found in Ollama — still allow if user insists (they may be about to pull it)
    logger.warning(f"⚠️ Model '{name}' not found in Ollama — switching anyway (may fail at inference)")
    model = name
    CURRENT_MODEL["name"] = name
    _persist_active_model(name)
    return model


def get_active_model() -> str:
    """Always returns the current active model name."""
    return model


def _load_ollama_conf(models_path: Path) -> dict:
    conf_path = models_path / "ollama.conf"
    if not conf_path.exists():
        logger.warning(f"⚠️ ollama.conf not found at {conf_path}, using defaults")
        return {}

    parser = configparser.ConfigParser()
    parser.read(conf_path)

    return {
        "OLLAMA_HOST":            f"http://{parser.get('server', 'host', fallback='127.0.0.1')}:{parser.get('server', 'port', fallback='11434')}",
        "OLLAMA_NUM_PARALLEL":    parser.getint("server", "num_parallel", fallback=1),
        "OLLAMA_MAX_QUEUE":       parser.getint("server", "max_queue", fallback=5),
        "OLLAMA_MODEL":           parser.get("models", "default", fallback=model),
        "OLLAMA_EMBEDDING_MODEL": parser.get("models", "embedding", fallback="all-mpnet-base-v2"),
    }


class Settings(BaseSettings):
    APP_NAME:    str  = "UniDocVerse AI Service"
    APP_VERSION: str  = "1.0.0"
    DEBUG:       bool = False

    DB_HOST:     str = "localhost"
    DB_PORT:     int = 54321
    DB_NAME:     str = "unidocverse_db"
    DB_USER:     str = "unidocverse"
    DB_PASSWORD: str = "unidocverse"

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    MODELS_PATH:   Path = Path("../models")
    UPLOAD_DIR:    Path = Path.home() / "Library/Application Support/UniDocVerse/storage/uploads"
    PROCESSED_DIR: Path = Path.home() / "Library/Application Support/UniDocVerse/storage/processed"

    MAX_FILE_SIZE_MB: int = 50

    ALLOWED_EXTENSIONS: list[str] = [
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
        ".html", ".htm", ".xlsx", ".xls", ".xlsm", ".csv", ".tsv",
        ".ods", ".parquet", ".json", ".jsonl", ".pptx", ".ppt",
        ".odp", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff",
        ".tif", ".webp", ".heic", ".heif", ".eml", ".msg", ".mbox",
        ".xml", ".yaml", ".yml",
    ]

    OLLAMA_HOST:            str = "http://127.0.0.1:11434"
    OLLAMA_MODEL:           str = model
    OLLAMA_EMBEDDING_MODEL: str = embedding
    OLLAMA_NUM_PARALLEL:    int = 1
    OLLAMA_MAX_QUEUE:       int = 5

    CORS_ORIGINS: list[str] = [
        "http://localhost:4200",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost:8080",
        "http://localhost",
        "https://unidocverse.com",
        "https://www.unidocverse.com",
        "https://staging.unidocverse.com",
    ]

    class Config:
        extra = 'ignore'
        env_file = ".env"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ollama.conf overlays on top of .env defaults
        ollama_conf = _load_ollama_conf(self.MODELS_PATH)
        for key, value in ollama_conf.items():
            object.__setattr__(self, key, value)

        # Platform-aware OCR
        system = platform.system().lower()
        if system in ("darwin", "windows", "linux"):
            os.environ["ENABLE_OCR"] = "true"
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "true"
            os.environ["OCR_MODE"] = "landingai"
            logger.info(f"🧠 OCR enabled ({system} desktop mode)")
        else:
            os.environ["ENABLE_OCR"] = "false"
            os.environ["OCR_MODE"] = "none"
            logger.info("🚫 OCR disabled (server mode)")


settings = Settings()

settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# Load active model from DB — overwrites module-level default
# Must run after settings so DB connection details are available
_load_active_model()