from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
from datetime import datetime


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    file_size: int
    status: str
    message: str


class DocumentStatusResponse(BaseModel):
    id: str
    filename: str
    status: str
    doc_type: Optional[str] = None
    has_summary: bool
    has_embedding: bool
    processed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_size: int
    doc_type: Optional[str] = None
    status: str
    summary: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class SearchResult(BaseModel):
    id: str
    filename: str
    doc_type: str
    summary: str
    similarity: float
    similarity_percent: str


class SemanticSearchResponse(BaseModel):
    query: str
    results: List[SearchResult]
    count: int


class SimilarDocumentsResponse(BaseModel):
    source_document: Dict[str, str]
    similar_documents: List[SearchResult]
    count: int
