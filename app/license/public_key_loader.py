from pathlib import Path

# Path to the PEM file inside app/license/
PUBLIC_KEY_PATH = Path(__file__).parent / "public_key.pem"

# Load the PEM contents
PUBLIC_KEY_PEM = PUBLIC_KEY_PATH.read_text(encoding="utf-8")
