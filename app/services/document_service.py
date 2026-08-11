import logging
from pathlib import Path
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.document import Document

logger = logging.getLogger(__name__)

# Initialize embedding model
#embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
#embedding_model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")

from app.config.model_factory import EmbeddingModelFactory

embedding_model = EmbeddingModelFactory.get_model()


class DocumentService:

    @staticmethod
    def get_documents(db: Session, skip: int = 0, limit: int = 100) -> List[Document]:
        """Get all documents with pagination"""
        return db.query(Document).order_by(
            Document.created_at.desc()
        ).offset(skip).limit(limit).all()

    @staticmethod
    def get_document_by_id(db: Session, doc_id: str) -> Optional[Document]:
        """Get document by ID"""
        return db.query(Document).filter(Document.id == doc_id).first()

    @staticmethod
    def delete_document(db: Session, doc_id: str) -> bool:
        """Delete document and its file"""
        doc = DocumentService.get_document_by_id(db, doc_id)
        if not doc:
            return False

        # Delete file from storage
        try:
            storage_path = Path("storage/uploads")
            for file in storage_path.glob(f"{doc_id}_*"):
                file.unlink()
                logger.info(f"Deleted file: {file}")
        except Exception as e:
            logger.error(f"Failed to delete file: {e}")

        # Delete from database
        db.delete(doc)
        db.commit()

        logger.info(f"Deleted document: {doc_id}")
        return True

    @staticmethod
    def semantic_search(db: Session, query: str, limit: int = 10) -> List[dict]:
        """Search documents using vector similarity"""

        # Generate query embedding
        query_embedding = embedding_model.encode(query)
        query_vector = query_embedding.tolist()

        logger.info(f"📊 Query vector: {len(query_vector)} dimensions")

        # Query using pgvector
        results = db.execute(
            text("""
                        SELECT 
                            id,
                            filename,
                            doc_type,
                            summary,
                            1 - (embedding <=> CAST(:query_vector AS vector)) as similarity
                        FROM documents
                        WHERE embedding IS NOT NULL
                        ORDER BY embedding <=> CAST(:query_vector AS vector)
                        LIMIT :limit
                    """),
            {
                "query_vector": str(query_vector),
                "limit": limit
            }
        ).fetchall()

        # Format results
        search_results = []
        for row in results:
            search_results.append({
                "id": str(row[0]),
                "filename": row[1],
                "doc_type": row[2] or "unknown",
                "summary": row[3] or "No summary available",
                "similarity": float(row[4]),
                "similarity_percent": f"{float(row[4]) * 100:.1f}%",

            })

        return search_results


    # @staticmethod
    # def find_similar_documents(db: Session, doc_id: str, limit: int = 5) -> List[dict]:
    #     """Find documents similar to a given document"""
    #     doc = DocumentService.get_document_by_id(db, doc_id)
    #
    #     emb = getattr(doc, "embedding", None) if doc is not None else None
    #     # if embedding is None or empty, bail out
    #     if doc is None or emb is None or (hasattr(emb, "__len__") and len(emb) == 0):
    #         return []
    #
    #     results = db.execute(
    #         text("""
    #             SELECT
    #                 id,
    #                 filename,
    #                 doc_type,
    #                 summary,
    #                 1 - (embedding <=> :source_vector) as similarity
    #             FROM documents
    #             WHERE embedding IS NOT NULL
    #               AND id != :source_id
    #             ORDER BY embedding <=> :source_vector
    #             LIMIT :limit
    #         """),
    #         {
    #             "source_vector": emb.tolist() if hasattr(emb, "tolist") else emb,
    #             "source_id": str(doc_id),
    #             "limit": int(limit),
    #         },
    #     ).fetchall()
    #
    #     similar_docs: List[dict] = []
    #     for row in results:
    #         similar_docs.append({
    #             "id": str(row[0]),
    #             "filename": row[1],
    #             "doc_type": row[2] or "unknown",
    #             "summary": row[3] or "No summary available",
    #             "similarity": float(row[4]),
    #             "similarity_percent": f"{float(row[4]) * 100:.1f}%",
    #         })
    #
    #     return similar_docs

    @staticmethod
    def find_similar_documents(db: Session, doc_id: str, limit: int = 5) -> List[dict]:
        """Find documents similar to a given document"""
        doc = DocumentService.get_document_by_id(db, doc_id)

        if doc.embedding is None:
            raise ValueError("Source document has no embedding")

        source_vector = (
            doc.embedding.tolist()
            if hasattr(doc.embedding, "tolist")
            else doc.embedding
        )

        vector_str = '[' + ','.join(str(x) for x in source_vector) + ']'

        results = db.execute(
            text("""
                SELECT
                    id,
                    filename,
                    doc_type,
                    summary,
                    1 - (embedding <=> CAST(:source_vector AS vector)) AS similarity
                FROM documents
                WHERE embedding IS NOT NULL
                  AND id != :source_id
                ORDER BY embedding <=> CAST(:source_vector AS vector)
                LIMIT :limit
            """),
            {
                "source_vector": vector_str,
                "source_id": str(doc_id),
                "limit": limit
            }
        ).fetchall()

        similar_docs = []
        for row in results:
            print(f"DEBUG similarity: {row.filename} = {float(row.similarity):.4f}")
            similarity = float(row.similarity)
            if similarity < 0.4:  # ← drop unrelated
                continue
            similar_docs.append({
                "id": str(row.id),
                "filename": row.filename,
                "doc_type": row.doc_type or "unknown",
                "summary": row.summary or "No summary available",
                "similarity": similarity,
                "similarity_percent": f"{similarity * 100:.1f}%"
            })

        return similar_docs

