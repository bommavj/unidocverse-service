from typing import TypedDict, Annotated, List, Dict, Any, Optional
import operator
from datetime import datetime


class DocumentState(TypedDict):
    """
    Shared state for the LangGraph workflow
    All agents read from and write to this state
    """

    # Document identification
    document_id: str
    filename: str
    file_path: str
    language: Optional[str]

    # File metadata
    file_size: int
    file_type: str
    mime_type: str

    # Extracted content
    raw_text: Optional[str]
    cleaned_text: Optional[str]
    tables: List[Dict[str, Any]]

    # AI Analysis
    classification: Optional[Dict[str, Any]]
    analyzed_data: Optional[Dict[str, Any]]  # ✅ THIS MUST BE HERE
    summary: Optional[str]
    key_points: List[str]
    keywords: List[str]

    # Embeddings
    embedding: Optional[List[float]]

    # Quality & Issues
    confidence_score: float
    anomalies: Annotated[List[Dict[str, Any]], operator.add]  # Can append from multiple agents
    quality_issues: Annotated[List[str], operator.add]

    # Metrics & Insights
    metrics: Dict[str, Any]
    insights: List[Dict[str, Any]]
    visualizations: List[Dict[str, Any]]

    # Processing control
    needs_ocr: bool
    needs_manual_review: bool
    requires_reprocessing: bool
    processing_complete: bool
    is_archive: Optional[bool]
    password_required: Optional[bool]

    # Metadata
    doc_metadata: Dict[str, Any]
    errors: Annotated[List[Dict[str, Any]], operator.add]

    # Timestamps
    started_at: str
    completed_at: Optional[str]