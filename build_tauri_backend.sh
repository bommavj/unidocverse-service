#!/bin/bash
# Build the PyInstaller binary for the Tauri desktop installer.
# Run from: unidocverse-service/
# Output goes to: ../unidocverse-installer/src-tauri/resources/backend/

set -e

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLER_RESOURCES="$SERVICE_DIR/../unidocverse-installer/src-tauri/resources"
DEST="$INSTALLER_RESOURCES/backend/unidocverse-backend"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
err()  { echo -e "${RED}✗ $1${NC}"; exit 1; }

echo "========================================="
echo "  UniDocVerse — Tauri Backend Build"
echo "  Embedding model: all-mpnet-base-v2 (768-dim)"
echo "========================================="

# ── 1. Validate model is present ─────────────────────────────────────────────
MODEL_DIR="$SERVICE_DIR/models/all-mpnet-base-v2"
if [ ! -d "$MODEL_DIR" ]; then
    err "Embedding model not found at: $MODEL_DIR\n  Run: python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2', cache_folder='models')\""
fi
ok "Embedding model found: all-mpnet-base-v2"

# Also ensure model is in Tauri resources
TAURI_MODEL="$INSTALLER_RESOURCES/models/all-mpnet-base-v2"
if [ ! -d "$TAURI_MODEL" ]; then
    warn "Copying model into Tauri resources..."
    mkdir -p "$TAURI_MODEL"
    cp -R "$MODEL_DIR/." "$TAURI_MODEL/"
    ok "Model copied to Tauri resources"
else
    ok "Model already in Tauri resources"
fi

# ── 2. Activate venv ──────────────────────────────────────────────────────────
VENV="$SERVICE_DIR/venv"
if [ ! -f "$VENV/bin/activate" ]; then
    err "venv not found at $VENV — run: python3 -m venv venv && pip install -r requirements.txt"
fi
source "$VENV/bin/activate"
ok "venv activated ($(python --version))"

# ── 3. Ensure PyInstaller is installed ───────────────────────────────────────
if ! python -m PyInstaller --version &>/dev/null; then
    warn "PyInstaller not found — installing..."
    pip install pyinstaller
fi
ok "PyInstaller $(python -m PyInstaller --version)"

# ── 4. Clean previous build ───────────────────────────────────────────────────
rm -rf "$SERVICE_DIR/build" "$SERVICE_DIR/dist"
ok "Cleaned previous build artifacts"

# ── 5. Run PyInstaller ────────────────────────────────────────────────────────
echo ""
echo "▶ Running PyInstaller (this takes a few minutes)..."
cd "$SERVICE_DIR"
python -m PyInstaller unidocverse-backend.spec \
    --noconfirm \
    --clean \
    --log-level WARN

BUILT="$SERVICE_DIR/dist/unidocverse-backend/unidocverse-backend"
if [ ! -f "$BUILT" ]; then
    err "Build failed — binary not found at $BUILT"
fi
ok "PyInstaller build complete"

# ── 6. Verify embedding dim via binary strings grep ──────────────────────────
echo "🔍 Verifying all-mpnet-base-v2 baked in..."
if strings "$BUILT" 2>/dev/null | grep -q "all-mpnet-base-v2"; then
    ok "all-mpnet-base-v2 confirmed in binary"
else
    warn "Could not confirm model name in binary strings (may still be correct)"
fi

# ── 7. Make universal binary (arm64 + x86_64) if on Apple Silicon ────────────
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ] && command -v lipo &>/dev/null; then
    # Check if x86_64 venv exists for cross-compile
    VENV_X86="$SERVICE_DIR/venv_x86_64"
    if [ -d "$VENV_X86" ]; then
        echo "🔄 Building x86_64 slice for universal binary..."
        source "$VENV_X86/bin/activate"
        python -m PyInstaller unidocverse-backend.spec \
            --noconfirm --clean --log-level WARN \
            --distpath dist_x86

        ARM_BIN="$SERVICE_DIR/dist/unidocverse-backend/unidocverse-backend"
        X86_BIN="$SERVICE_DIR/dist_x86/unidocverse-backend/unidocverse-backend"
        lipo -create -output "$ARM_BIN.universal" "$ARM_BIN" "$X86_BIN"
        mv "$ARM_BIN.universal" "$ARM_BIN"
        source "$VENV/bin/activate"
        ok "Universal binary created (arm64 + x86_64)"
    else
        warn "venv_x86_64 not found — building arm64 only (Intel Macs won't run this)"
    fi
fi

# ── 8. Copy binary to Tauri resources ────────────────────────────────────────
echo ""
echo "📦 Copying binary to Tauri resources..."
mkdir -p "$(dirname "$DEST")"
cp "$BUILT" "$DEST"
chmod +x "$DEST"

# Verify
ARCHS=$(lipo -archs "$DEST" 2>/dev/null || file "$DEST" | grep -oE "arm64|x86_64" | tr '\n' ' ')
ok "Binary installed: $DEST"
ok "Architectures: ${ARCHS:-unknown}"

# ── 9. Copy migrations (V30, V31) to Tauri resources ─────────────────────────
TAURI_MIGRATIONS="$INSTALLER_RESOURCES/migrations/sql"
for v in V30 V31; do
    SRC=$(ls "$SERVICE_DIR/migrations/sql/${v}__"*.sql 2>/dev/null | head -1)
    if [ -n "$SRC" ]; then
        cp "$SRC" "$TAURI_MIGRATIONS/"
        ok "Migration synced: $(basename "$SRC")"
    fi
done

# ── 10. Summary ───────────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo -e "${GREEN}  Build complete!${NC}"
echo "  Binary : $DEST"
echo "  Model  : all-mpnet-base-v2 (768-dim)"
echo ""
echo "  Next steps:"
echo "  1. cd ../unidocverse-installer"
echo "  2. npm run tauri build"
echo "========================================="
