#!/bin/bash

echo "🚀 Starting UniDocVerse with PaddleX disabled..."

# ============================================================
# MUST BE SET BEFORE PYTHON STARTS — THIS IS THE FIX
# ============================================================
export USE_PADDLEX=0
export PADDLEOCR_USE_PADDLEX=0
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=true
export FLAGS_use_mkldnn=0
export FLAGS_allocator_strategy=naive_best_fit

# Optional: show env vars for debugging
echo "USE_PADDLEX=$USE_PADDLEX"
echo "PADDLEOCR_USE_PADDLEX=$PADDLEOCR_USE_PADDLEX"
echo "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=$PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"

# ============================================================
# ACTIVATE VENV
# ============================================================
source venv/bin/activate

# ============================================================
# RUN BACKEND
# ============================================================
uvicorn app.main:app --host 0.0.0.0 --port 8000
