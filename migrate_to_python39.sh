#!/bin/bash

echo "🚀 UniDocVerse — Python 3.9 Migration Script (macOS ARM)"
echo "========================================================"

# 1. Ensure pyenv is initialized
echo "🔧 Initializing pyenv..."
export PATH="$HOME/.pyenv/bin:$PATH"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"

# 2. Install Python 3.9.19
echo "📦 Installing Python 3.9.19..."
pyenv install -s 3.9.19

# 3. Set local Python version
echo "🔧 Setting local Python version to 3.9.19..."
pyenv local 3.9.19

# 4. Remove old venv
echo "🧹 Removing old venv..."
rm -rf venv

# 5. Create new venv
echo "🐍 Creating new Python 3.9 virtualenv..."
python3.9 -m venv venv

# 6. Activate venv
echo "🔌 Activating virtualenv..."
source venv/bin/activate

# 7. Upgrade pip
echo "⬆️ Upgrading pip..."
pip install --upgrade pip setuptools wheel

# 8. Install PaddlePaddle 2.5.2 (macOS ARM wheel)
echo "📦 Installing PaddlePaddle 2.5.2 (macOS ARM)..."
pip install https://paddle-wheel.bj.bcebos.com/2.5.2/macos/arm64/paddlepaddle-2.5.2-cp39-cp39-macosx_11_0_arm64.whl

# 9. Install PaddleOCR 2.7.0.3
echo "📦 Installing PaddleOCR 2.7.0.3..."
pip install paddleocr==2.7.0.3

# 10. Install remaining requirements
echo "📦 Installing remaining requirements..."
pip install -r requirements.txt

echo "🎉 Migration complete!"
echo "👉 Run: source venv/bin/activate"
echo "👉 Then start your backend normally."
