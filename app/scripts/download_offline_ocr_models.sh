#!/bin/bash
set -e

BASE_DIR="models"
OCR_DIR="$BASE_DIR/ocr"
LAYOUT_DIR="$BASE_DIR/layout"

echo "📦 Creating model directories..."
mkdir -p "$OCR_DIR/det" "$OCR_DIR/rec" "$OCR_DIR/cls"
mkdir -p "$LAYOUT_DIR/picodet_lcnet_x1_0_fgd_layout_infer"

echo "⬇️ Downloading OFFLINE PaddleOCR models..."

# DETECTION MODEL (PP-OCRv3)
curl -L -o "$OCR_DIR/det/inference.pdmodel" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/chinese/ch_PP-OCRv3_det_infer/inference.pdmodel
curl -L -o "$OCR_DIR/det/inference.pdiparams" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/chinese/ch_PP-OCRv3_det_infer/inference.pdiparams
curl -L -o "$OCR_DIR/det/inference.pdiparams.info" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/chinese/ch_PP-OCRv3_det_infer/inference.pdiparams.info

# RECOGNITION MODEL (ENGLISH)
curl -L -o "$OCR_DIR/rec/inference.pdmodel" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_rec_infer/inference.pdmodel
curl -L -o "$OCR_DIR/rec/inference.pdiparams" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_rec_infer/inference.pdiparams
curl -L -o "$OCR_DIR/rec/inference.pdiparams.info" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_rec_infer/inference.pdiparams.info

# CLS MODEL
curl -L -o "$OCR_DIR/cls/inference.pdmodel" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/chinese/ch_ppocr_mobile_v2.0_cls_infer/inference.pdmodel
curl -L -o "$OCR_DIR/cls/inference.pdiparams" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/chinese/ch_ppocr_mobile_v2.0_cls_infer/inference.pdiparams
curl -L -o "$OCR_DIR/cls/inference.pdiparams.info" \
  https://paddleocr.bj.bcebos.com/PP-OCRv3/chinese/ch_ppocr_mobile_v2.0_cls_infer/inference.pdiparams.info

echo "⬇️ Downloading OFFLINE PaddleDetection layout model..."

curl -L -o "$LAYOUT_DIR/picodet_lcnet_x1_0_fgd_layout_infer/inference.pdmodel" \
  https://paddle-model-ecology.bj.bcebos.com/layout/picodet_lcnet_x1_0_fgd_layout_infer/inference.pdmodel
curl -L -o "$LAYOUT_DIR/picodet_lcnet_x1_0_fgd_layout_infer/inference.pdiparams" \
  https://paddle-model-ecology.bj.bcebos.com/layout/picodet_lcnet_x1_0_fgd_layout_infer/inference.pdiparams
curl -L -o "$LAYOUT_DIR/picodet_lcnet_x1_0_fgd_layout_infer/inference.pdiparams.info" \
  https://paddle-model-ecology.bj.bcebos.com/layout/picodet_lcnet_x1_0_fgd_layout_infer/inference.pdiparams.info

echo "🎉 Offline OCR models downloaded successfully!"
