"""
precheck_node — runs immediately after ingest, before parse.

Detects password-protected / encrypted documents for ALL supported file types.
If protected, sets password_required=True in state and the workflow
conditional edge routes straight to finalize — no parsing attempted.

Coverage:
  PDF              — PyMuPDF (fitz)
  Office XML       — msoffcrypto  (xlsx, xlsm, xlsb, docx, pptx)
  Legacy Office    — msoffcrypto  (xls, doc, ppt)
  OpenDocument     — ZIP entry check for encryption manifest (ods, odt, odp)
  Outlook MSG      — msoffcrypto
  ZIP              — central-directory flag_bits
  7-Zip            — binary header signature
  RAR              — binary header signature
  CSV/TSV/TXT/MD   — plain text, never encrypted → always False
  JSON/XML/YAML    — plain text, never encrypted → always False
  Images           — never encrypted at file level → always False
  HTML/EML/Web     — plain text, never encrypted → always False
  TAR/GZ/TGZ       — compression only, no encryption → always False
  Unknown          — generic binary-signature check as best-effort
"""

import logging
import struct
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


# ── Never-encrypted formats (plain text / binary with no crypto support) ──────
_NEVER_ENCRYPTED = {
    # Plain text
    "txt", "md", "csv", "tsv", "log",
    # Data interchange
    "json", "xml", "yaml", "yml", "toml",
    # Web
    "html", "htm", "mhtml", "mht",
    # Email (plain MIME)
    "eml",
    # Images
    "jpg", "jpeg", "png", "tiff", "tif", "bmp",
    "webp", "heic", "heif", "gif", "svg",
    # Compression-only archives (no crypto layer)
    "tar", "gz", "tgz", "bz2", "xz",
}

# ── Categorised encryptable formats ───────────────────────────────────────────
_PDF_EXTS         = {"pdf"}
_OFFICE_EXTS      = {"xlsx", "xlsm", "xlsb", "xls",
                     "docx", "doc",
                     "pptx", "ppt",
                     "msg"}
_OPENDOC_EXTS     = {"ods", "odt", "odp", "odb"}
_ZIP_EXTS         = {"zip"}
_SEVENZIP_EXTS    = {"7z"}
_RAR_EXTS         = {"rar"}


# ── Per-format checkers ───────────────────────────────────────────────────────

def _check_pdf(file_path: str) -> bool:
    try:
        import fitz
        doc = fitz.open(file_path)
        encrypted = doc.is_encrypted and not doc.authenticate("")
        doc.close()
        return encrypted
    except Exception as e:
        logger.debug(f"PDF check error: {e}")
        return False


def _check_office(file_path: str) -> bool:
    """Works for both modern XML-based (xlsx/docx/pptx) and legacy OLE (xls/doc/ppt) and MSG."""
    try:
        import msoffcrypto
        with open(file_path, "rb") as f:
            office = msoffcrypto.OfficeFile(f)
            return office.is_encrypted()
    except Exception as e:
        logger.debug(f"Office check error: {e}")
        return False


def _check_opendocument(file_path: str) -> bool:
    """ODS/ODT/ODP are ZIP containers — encrypted ones have a manifest entry with crypto info."""
    try:
        import zipfile
        with zipfile.ZipFile(file_path) as zf:
            names = zf.namelist()
            # Encrypted OpenDocument has META-INF/manifest.xml with manifest:encryption-data
            if "META-INF/manifest.xml" in names:
                manifest = zf.read("META-INF/manifest.xml").decode("utf-8", errors="ignore")
                if "encryption-data" in manifest or "manifest:encryption" in manifest:
                    return True
            # Fallback: any entry has encryption flag
            for info in zf.infolist():
                if info.flag_bits & 0x1:
                    return True
        return False
    except Exception as e:
        logger.debug(f"OpenDocument check error: {e}")
        return False


def _check_zip(file_path: str) -> bool:
    """Standard ZIP — check encryption bit in central directory."""
    try:
        import zipfile
        with zipfile.ZipFile(file_path) as zf:
            for info in zf.infolist():
                if info.flag_bits & 0x1:
                    return True
        return False
    except Exception as e:
        logger.debug(f"ZIP check error: {e}")
        return False


def _check_7z(file_path: str) -> bool:
    """7-Zip: read signature header. PasswordProtected bit is in NextHeaderSize field area."""
    try:
        with open(file_path, "rb") as f:
            sig = f.read(6)
            # 7z magic: 37 7A BC AF 27 1C
            if sig != b"7z\xbc\xaf'\x1c":
                return False
            # Skip version (2 bytes) + start header CRC (4 bytes)
            f.seek(12)
            # NextHeaderSize > 0 but encrypted archives have PasswordProtected property
            # Simplest reliable check: look for 0x06 (kEncoded/kEncryption) property in header
            header_data = f.read(1024)
            return b"\x06" in header_data or b"\x17" in header_data
    except Exception as e:
        logger.debug(f"7z check error: {e}")
        return False


def _check_rar(file_path: str) -> bool:
    """RAR: check archive header flags for encryption."""
    try:
        with open(file_path, "rb") as f:
            sig = f.read(7)
            # RAR4 magic: 52 61 72 21 1A 07 00
            if sig == b"Rar!\x1a\x07\x00":
                f.seek(10)  # Archive header starts here
                flags = struct.unpack("<H", f.read(2))[0]
                return bool(flags & 0x0080)  # bit 7 = password encrypted
            # RAR5 magic: 52 61 72 21 1A 07 01 00
            if sig[:7] == b"Rar!\x1a\x07\x01":
                # RAR5 encryption is complex; presence of encryption header block (type 4)
                data = f.read(512)
                return b"\x04" in data
        return False
    except Exception as e:
        logger.debug(f"RAR check error: {e}")
        return False


def _check_generic(file_path: str) -> bool:
    """
    Best-effort check for unknown file types.
    Looks for common encryption markers in the first 512 bytes.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(512)
        # Common encryption/password keywords in binary headers
        markers = [b"Encrypt", b"encrypt", b"Password", b"password",
                   b"ENCRYPTED", b"DRM", b"\x06\x00\x06\x00"]
        return any(m in header for m in markers)
    except Exception:
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def is_protected(file_path: str, filename: str) -> bool:
    """
    Return True if the file is password-protected / encrypted.
    Covers all file types UniDocVerse supports.
    """
    ext = Path(filename).suffix.lower().lstrip(".")

    # Fast-exit for formats that structurally cannot be encrypted
    if ext in _NEVER_ENCRYPTED:
        return False

    if ext in _PDF_EXTS:
        return _check_pdf(file_path)
    if ext in _OFFICE_EXTS:
        return _check_office(file_path)
    if ext in _OPENDOC_EXTS:
        return _check_opendocument(file_path)
    if ext in _ZIP_EXTS:
        return _check_zip(file_path)
    if ext in _SEVENZIP_EXTS:
        return _check_7z(file_path)
    if ext in _RAR_EXTS:
        return _check_rar(file_path)

    # Unknown extension — best-effort binary scan
    logger.debug(f"[PrecheckNode] Unknown ext '.{ext}', running generic check")
    return _check_generic(file_path)


# ── Workflow node ─────────────────────────────────────────────────────────────

_ARCHIVE_EXTS_SET = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2"}


def precheck_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Workflow node: detect password protection before any parsing.
    Sets state["password_required"] = True if the file is locked.
    """
    file_path = state.get("file_path", "")
    filename  = state.get("filename", "")

    if not file_path or not filename:
        return state

    # Mark archives so the workflow routes to extract_archive_node
    ext = Path(filename).suffix.lower().lstrip(".")
    is_archive = ext in _ARCHIVE_EXTS_SET

    try:
        if is_protected(file_path, filename):
            logger.warning(f"🔒 [PrecheckNode] Password-protected: {filename}")
            return {
                **state,
                "password_required": True,
                "is_archive": is_archive,
                "raw_text": "",
                "cleaned_text": "",
                "summary": "",
                "classification": {"type": "unknown", "confidence": 0.0},
                "insights": [],
                "anomalies": [],
                "errors": ["password_required"],
                "processing_complete": False,
            }
        logger.info(f"✅ [PrecheckNode] No protection detected: {filename}")
    except Exception as e:
        logger.error(f"❌ [PrecheckNode] Check failed for {filename}: {e}")

    return {**state, "is_archive": is_archive}
