"""Test HEIC receipt search"""
import requests
from app.core.database import SessionLocal
from sqlalchemy import text

BASE_URL = "http://localhost:8000"

print("=" * 70)
print("HEIC RECEIPT SEARCH DIAGNOSTIC")
print("=" * 70)

# 1. Check for image documents
print("\n1️⃣ Checking for image documents...")
db = SessionLocal()

result = db.execute(text("""
    SELECT 
        filename,
        doc_type,
        status,
        LENGTH(raw_text) as text_len,
        CASE WHEN embedding IS NOT NULL THEN 'YES' ELSE 'NO' END as has_emb
    FROM documents
    WHERE 
        filename ILIKE '%.heic' 
        OR filename ILIKE '%.jpg'
        OR doc_type LIKE '%receipt%'
        OR doc_type LIKE '%image%'
    ORDER BY created_at DESC
"""))

docs = result.fetchall()
db.close()

if not docs:
    print("   ❌ No image/receipt documents found")
    exit(1)

print(f"   ✅ Found {len(docs)} image/receipt document(s):")
for doc in docs:
    print(f"      - {doc.filename}")
    print(f"        Type: {doc.doc_type}, Status: {doc.status}")
    print(f"        Text: {doc.text_len} chars, Embedding: {doc.has_emb}")

# 2. Check search config
print("\n2️⃣ Checking search configuration...")
response = requests.get(f"{BASE_URL}/api/search/test")
config = response.json()
print(f"   Documents with embeddings: {config.get('documents_with_embeddings')}")
print(f"   Model: {config.get('embedding_model')}")

# 3. Try different search terms
print("\n3️⃣ Testing searches...")
search_terms = ['tomato', 'receipt', 'costco', 'groceries', 'food', 'vegetables']

for term in search_terms:
    response = requests.get(
        f"{BASE_URL}/api/search/semantic",
        params={'query': term, 'limit': 3, 'threshold': 0.3}
    )
    results = response.json()

    if results['count'] > 0:
        print(f"   ✅ '{term}': {results['count']} results")
        for r in results['results'][:1]:
            print(f"      → {r['filename']} (similarity: {r['similarity']:.3f})")
    else:
        print(f"   ❌ '{term}': No results")

print("\n" + "=" * 70)