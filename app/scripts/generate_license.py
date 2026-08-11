#!/usr/bin/env python3
"""
UniDocVerse License Generator
Generates signed license files for customers
"""
import json
import base64
from datetime import datetime, timedelta
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import sys

PRIVATE_KEY_PATH = Path("private_key.pem")


def load_private_key():
    """Load the private key for signing"""
    if not PRIVATE_KEY_PATH.exists():
        print("❌ Error: private_key.pem not found!")
        print("   Run: python scripts/generate_pem_files.py first")
        sys.exit(1)

    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def canonical_payload(license_data: dict) -> bytes:
    """Create canonical payload for signing (excluding signature)"""
    data = {k: v for k, v in license_data.items() if k != "signature"}
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_license(license_data: dict) -> str:
    """Sign the license data with private key"""
    private_key = load_private_key()
    payload = canonical_payload(license_data)

    signature = private_key.sign(
        payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    return base64.b64encode(signature).decode("utf-8")

VERSION = 1

def generate_license(
        email: str,
        issued_to: str,
        plan: str = "Pro",
        days_valid: int = None
) -> dict:
    """
    Generate a signed license file

    Args:
        email: Customer email
        issued_to: Customer name
        plan: License plan (Pro, Enterprise, etc.)
        days_valid: Number of days valid (None = lifetime)

    Returns:
        License data dict with signature
    """
    # Create license data
    license_data = {
        "issued_to": issued_to,
        "email": email.lower().strip(),
        "plan": plan,
        "issued_at": datetime.utcnow().isoformat() + "Z",
        "expiry": None if days_valid is None else (
                                                          datetime.utcnow() + timedelta(days=days_valid)
                                                  ).isoformat() + "Z",
        "version": VERSION,
        "app": "UniDocVerse"
    }

    # Sign the license
    license_data["signature"] = sign_license(license_data)

    return license_data


def save_license_file(license_data: dict, output_path: str):
    """Save license to a JSON file"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(license_data, f, indent=2)


def main():
    """CLI interface for generating licenses"""
    print("🔑 UniDocVerse License Generator")
    print("=" * 60)

    # Check for command line arguments
    # if len(sys.argv) < 3:
    #     print("\nUsage:")
    #     print("  python generate_license.py <email> <name> [days_valid]")
    #     print("\nExamples:")
    #     print("  # Lifetime license")
    #     print('  python generate_license.py customer@email.com "John Doe"')
    #     print("\n  # 365-day license")
    #     print('  python generate_license.py trial@company.com "Trial User" 365')
    #     sys.exit(1)
    #
    # email = sys.argv[1]
    # issued_to = sys.argv[2]
    # days_valid = int(sys.argv[3]) if len(sys.argv) > 3 else None

    ##hardcoding for now:
    email = "navyatha.bomma@gmail.com "
    issued_to = "Navya Bomma"
    organization = "Home"
    usecase = "Enterprise"

    days_valid = 90

    # Generate license
    print(f"\n📝 Generating license for:")
    print(f"   Email: {email}")
    print(f"   Name:  {issued_to}")
    print(f"   Type:  {'Lifetime' if days_valid is None else f'{days_valid} days'}")
    print()

    license_data = generate_license(email, issued_to, days_valid=days_valid)

    # Save to file
    output_filename = f"license_{email.replace('@', '_').replace('.', '_')}.udv.json"
    save_license_file(license_data, output_filename)

    print(f"✅ License generated successfully!")
    print(f"📁 Saved to: {output_filename}")
    print()
    print("📋 License Details:")
    print(f"   Issued To:  {license_data['issued_to']}")
    print(f"   Email:      {license_data['email']}")
    print(f"   Plan:       {license_data['plan']}")
    print(f"   Issued At:  {license_data['issued_at']}")
    print(f"   Expires:    {license_data['expiry'] or 'Never (Lifetime)'}")
    print()
    print("📧 Send this file to the customer to upload in UniDocVerse")
    print()


if __name__ == "__main__":
    main()