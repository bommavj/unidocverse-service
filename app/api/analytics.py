import json
import os

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, List, Dict
import pandas as pd
from io import BytesIO
from datetime import datetime
import logging

from app.core.database import get_db
from app.models.document import Document
from app.services.advanced.advanced_dashboard import generate_dashboard_analytics
from app.services.advanced.advanced_dashboard_ui_config import generate_ui_config_v2
from app.services.analytics_engine import UniversalAnalyticsEngine
import pdfplumber
import tabula
from typing import Optional
import pandas as pd

from app.services.parsers.universal_parser import UniversalParser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# Initialize universal engine
analytics_engine = UniversalAnalyticsEngine()


# app/routers/analytics.py (add this new endpoint)

@router.get("/summary")
async def get_analytics_summary(db: Session = Depends(get_db)):
    """
    Get overall analytics summary for dashboard
    """
    from datetime import datetime, timedelta
    from sqlalchemy import func, extract
    from decimal import Decimal

    # Total documents
    total_docs = db.query(func.count(Document.id)).scalar() or 0

    # Success rate (documents with status 'completed')
    completed = db.query(func.count(Document.id)).filter(
        Document.status == 'completed'
    ).scalar() or 0
    success_rate = (completed / total_docs * 100) if total_docs > 0 else 0

    # Avg processing time (mock for now - you can add this field to Document model)
    avg_time = 2.3  # seconds

    # ✅ FIX: Storage (sum of file sizes) - convert Decimal to float
    total_storage = db.query(func.sum(Document.file_size)).scalar() or 0
    total_storage = float(total_storage) if isinstance(total_storage, Decimal) else total_storage
    total_storage_gb = total_storage / (1024 ** 3)  # Convert to GB

    # Document types distribution
    doc_types = db.query(
        Document.doc_type,
        func.count(Document.id).label('count')
    ).group_by(Document.doc_type).all()

    document_types = {doc_type or 'Unknown': count for doc_type, count in doc_types}

    # Upload trend (last 6 months) - PostgreSQL compatible
    six_months_ago = datetime.utcnow() - timedelta(days=180)

    monthly_uploads = db.query(
        func.to_char(Document.created_at, 'YYYY-MM').label('month'),
        func.count(Document.id).label('count')
    ).filter(
        Document.created_at >= six_months_ago
    ).group_by('month').order_by('month').all()

    # Format labels nicely (e.g., "2024-08" -> "Aug 2024")
    from datetime import datetime as dt
    upload_labels = []
    upload_data = []

    for month_str, count in monthly_uploads:
        try:
            month_obj = dt.strptime(month_str, '%Y-%m')
            upload_labels.append(month_obj.strftime('%b %Y'))  # "Aug 2024"
        except:
            upload_labels.append(month_str)
        upload_data.append(count)

    upload_trend = {
        "labels": upload_labels,
        "data": upload_data
    }

    # Processing status
    processing_status = {
        "completed": db.query(func.count(Document.id)).filter(Document.status == 'completed').scalar() or 0,
        "processing": db.query(func.count(Document.id)).filter(Document.status == 'processing').scalar() or 0,
        "failed": db.query(func.count(Document.id)).filter(Document.status == 'failed').scalar() or 0,
        "pending": db.query(func.count(Document.id)).filter(Document.status == 'pending').scalar() or 0,
    }

    # ✅ FIX: Calculate storage change (now works with float)
    storage_growth = round(total_storage_gb * 0.2, 1) if total_storage_gb > 0 else 0

    return {
        "summary": {
            "total_documents": total_docs,
            "success_rate": round(success_rate, 1),
            "avg_processing_time": avg_time,
            "total_storage_gb": round(total_storage_gb, 2),
            "total_documents_change": "+12%",  # Calculate from historical data later
            "success_rate_change": "+2.1%",
            "avg_time_change": "-0.5s",
            "storage_change": f"+{storage_growth} GB"
        },
        "document_types": document_types,
        "upload_trend": upload_trend,
        "processing_status": processing_status
    }


@router.get("/transactions")
async def get_transactions(
    limit: int = 15,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Paginated transaction list from all documents."""
    import re

    def _clean_merchant(raw: str) -> str:
        if not raw:
            return "Unknown"
        s = raw.replace("/", "")
        s = re.sub(r'\s+DES:.*', '', s)
        s = re.sub(r'\s+ID:.*', '', s)
        s = re.sub(r'\s+INDN:.*', '', s)
        words = s.split()[:3]
        return " ".join(w.title() for w in words) if words else "Unknown"

    safe_limit  = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    total = db.execute(text("""
        SELECT COUNT(*) FROM documents d,
               jsonb_array_elements(d.extracted_facts->'transactions') t
        WHERE d.extracted_facts->'transactions' IS NOT NULL
          AND (t->>'amount') IS NOT NULL AND (t->>'amount') != ''
    """)).scalar() or 0

    rows = db.execute(text("""
        SELECT
            d.filename,
            t->>'date'     AS date,
            t->>'merchant' AS merchant,
            t->>'type'     AS type,
            (t->>'amount')::numeric AS amount
        FROM documents d,
             jsonb_array_elements(d.extracted_facts->'transactions') t
        WHERE d.extracted_facts->'transactions' IS NOT NULL
          AND (t->>'amount') IS NOT NULL AND (t->>'amount') != ''
        ORDER BY t->>'date' DESC
        LIMIT :limit OFFSET :offset
    """), {"limit": safe_limit, "offset": safe_offset}).fetchall()

    items = [{
        "date":     r.date or "",
        "merchant": _clean_merchant(r.merchant or ""),
        "amount":   round(float(r.amount or 0), 2),
        "type":     (r.type or "debit").lower(),
        "doc":      r.filename or "",
    } for r in rows]

    return {
        "items":    items,
        "total":    total,
        "limit":    safe_limit,
        "offset":   safe_offset,
        "has_more": (safe_offset + len(items)) < total,
    }


@router.get("/financial")
async def get_financial_analytics(db: Session = Depends(get_db)):
    """
    Aggregate all transaction data from bank/CC statement extracted_facts.
    Returns KPIs, monthly income vs spend, top merchants, recent transactions.
    """
    import re
    from collections import defaultdict
    from decimal import Decimal

    def _clean_merchant(raw: str) -> str:
        if not raw:
            return "Unknown"
        # Remove forward slashes used as line-break artifacts
        s = raw.replace("/", "")
        # Strip ACH/PPD noise after DES: or ID:
        s = re.sub(r'\s+DES:.*', '', s)
        s = re.sub(r'\s+ID:.*', '', s)
        s = re.sub(r'\s+INDN:.*', '', s)
        # Take first 3 words, title-case
        words = s.split()[:3]
        return " ".join(w.title() for w in words) if words else "Unknown"

    rows = db.execute(text("""
        SELECT
            d.filename,
            t->>'date'     AS date,
            t->>'merchant' AS merchant,
            t->>'type'     AS type,
            (t->>'amount')::numeric AS amount
        FROM documents d,
             jsonb_array_elements(d.extracted_facts->'transactions') t
        WHERE d.extracted_facts->'transactions' IS NOT NULL
          AND (t->>'amount') IS NOT NULL
          AND (t->>'amount') != ''
        ORDER BY t->>'date' DESC
    """)).fetchall()

    total_txns = len(rows)
    total_spend = 0.0
    total_income = 0.0
    monthly: dict = defaultdict(lambda: {"spend": 0.0, "income": 0.0})
    merchant_spend: dict = defaultdict(float)
    doc_spend: dict = defaultdict(float)
    recent = []

    for r in rows:
        amt = float(r.amount or 0)
        txn_type = (r.type or "debit").lower()
        month = (r.date or "")[:7]
        merchant = _clean_merchant(r.merchant or "")
        doc = r.filename or "Unknown"

        if txn_type == "credit":
            total_income += amt
            if month:
                monthly[month]["income"] += amt
        else:
            total_spend += amt
            if month:
                monthly[month]["spend"] += amt
            merchant_spend[merchant] += amt
            doc_spend[doc] += amt

        if len(recent) < 10:
            recent.append({
                "date":     r.date or "",
                "merchant": merchant,
                "amount":   round(amt, 2),
                "type":     txn_type,
                "doc":      doc,
            })

    # Monthly chart — sorted chronologically, filter out bad months
    valid_months = sorted(k for k in monthly if len(k) == 7 and k[:4].isdigit())
    monthly_labels = [m for m in valid_months]
    monthly_spend  = [round(monthly[m]["spend"], 2)  for m in valid_months]
    monthly_income = [round(monthly[m]["income"], 2) for m in valid_months]

    # Top 10 merchants by spend
    top_merchants = sorted(merchant_spend.items(), key=lambda x: x[1], reverse=True)[:10]

    # By document
    docs_sorted = sorted(doc_spend.items(), key=lambda x: x[1], reverse=True)

    return {
        "kpis": {
            "total_transactions": total_txns,
            "total_spend":        round(total_spend, 2),
            "total_income":       round(total_income, 2),
            "net":                round(total_income - total_spend, 2),
        },
        "monthly": {
            "labels": monthly_labels,
            "spend":  monthly_spend,
            "income": monthly_income,
        },
        "top_merchants": [
            {"label": k, "amount": round(v, 2)} for k, v in top_merchants
        ],
        "by_doc": [
            {"label": k, "amount": round(v, 2)} for k, v in docs_sorted
        ],
        "recent": recent,
    }


@router.get("/{doc_id}/debug-metadata")
async def debug_metadata(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Debug endpoint to see what's actually in the metadata
    """
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()

        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        return {
            "document_id": doc_id,
            "filename": doc.filename,
            "doc_type": doc.doc_type,
            "has_metadata": doc.doc_metadata is not None,
            "metadata_keys": list(doc.doc_metadata.keys()) if doc.doc_metadata else [],
            "metadata_preview": {
                k: f"{type(v).__name__} ({len(v) if isinstance(v, (list, dict, str)) else 'N/A'})"
                for k, v in (doc.doc_metadata or {}).items()
            },
            "full_metadata": doc.doc_metadata
        }
    except Exception as e:
        logger.error(f"Debug error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schema")
async def get_data_schema(
        doc_id: Optional[str] = None,
        db: Session = Depends(get_db)
):
    """
    Get schema of document data - completely dynamic
    Returns column types, suggested analytics, etc.
    """

    try:
        df = analytics_engine.get_document_data(db, doc_id)
        schema = analytics_engine.analyze_schema(df)

        return {
            "schema": schema,
            "message": "Schema analyzed successfully"
        }

    except Exception as e:
        logger.error(f"Schema analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/query")
async def natural_language_query(
        q: str = Query(..., description="Natural language query", alias="query"),
        doc_id: Optional[str] = None,
        db: Session = Depends(get_db)
):
    """
    Universal natural language query endpoint

    Works with ANY data - no hardcoding!

    Examples:
    - "How many sales did Bob make?"
    - "Total revenue by region"
    - "Top 5 products"
    - "Average price for laptops"
    - "Show me all orders from East region"
    """

    try:
        result = analytics_engine.query(db, q, doc_id)
        return result

    except Exception as e:
        logger.error(f"Query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
async def export_results(
        query: str = Query(..., description="Natural language query"),
        format: str = Query("xlsx", description="Export format: xlsx, csv"),
        doc_id: Optional[str] = None,
        db: Session = Depends(get_db)
):
    """Export query results to Excel or CSV"""

    try:
        # Execute query
        result = analytics_engine.query(db, query, doc_id)
        df = pd.DataFrame(result['results'])

        if format == "csv":
            output = BytesIO()
            df.to_csv(output, index=False)
            output.seek(0)

            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                }
            )

        else:  # xlsx
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Results', index=False)
            output.seek(0)

            return Response(
                content=output.getvalue(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": f"attachment; filename=export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                }
            )

    except Exception as e:
        logger.error(f"Export error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suggestions")
async def get_query_suggestions(
        doc_id: Optional[str] = None,
        db: Session = Depends(get_db)
):
    """
    Get suggested queries based on the data
    Completely dynamic - no hardcoding!
    """

    try:
        df = analytics_engine.get_document_data(db, doc_id)
        schema = analytics_engine.analyze_schema(df)

        suggestions = []

        # Generate suggestions based on schema
        for agg in schema['suggested_aggregations']:
            suggestions.append({
                "query": f"What is the {agg['label']}?",
                "category": "aggregation"
            })

        for group in schema['suggested_groupings']:
            if schema['numeric_columns']:
                suggestions.append({
                    "query": f"Show me total {schema['numeric_columns'][0]} {group['label']}",
                    "category": "breakdown"
                })

        # Top N queries
        if schema['categorical_columns'] and schema['numeric_columns']:
            cat_col = schema['categorical_columns'][0]
            num_col = schema['numeric_columns'][0]
            suggestions.append({
                "query": f"Top 10 {cat_col} by {num_col}",
                "category": "ranking"
            })

        return {
            "suggestions": suggestions[:10],  # Limit to 10
            "schema_summary": {
                "total_rows": schema['row_count'],
                "total_columns": len(schema['columns']),
                "categorical_columns": schema['categorical_columns'],
                "numeric_columns": schema['numeric_columns']
            }
        }

    except Exception as e:
        logger.error(f"Suggestions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/{doc_id}")
async def get_document_dashboard(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Get complete analytics dashboard for a document
    Returns KPIs, charts, trends, and insights
    """

    try:
        result = analytics_engine.get_dashboard_data(db, doc_id)
        return result

    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard")
async def get_all_documents_dashboard(
        db: Session = Depends(get_db)
):
    """Get dashboard for all documents combined"""

    try:
        result = analytics_engine.get_dashboard_data(db, None)
        return result

    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{doc_id}/dashboard")
async def get_document_dashboard(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Get universal dashboard for ANY document type

    Automatically generates appropriate visualizations based on document type
    """
    from app.services.analytics_engine import UniversalAnalyticsEngine

    try:
        analytics_engine = UniversalAnalyticsEngine()
        dashboard = analytics_engine.get_dashboard_data(db, doc_id)

        return dashboard

    except Exception as e:
        logger.error(f"❌ Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


import math
import numpy as np


def sanitize_for_json(obj):
    """Recursively sanitize any object so FastAPI can JSON encode it safely."""

    # Handle dict
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}

    # Handle list/tuple/set
    if isinstance(obj, (list, tuple, set)):
        return [sanitize_for_json(v) for v in obj]

    # Handle numpy arrays
    if isinstance(obj, np.ndarray):
        return sanitize_for_json(obj.tolist())

    # Handle numpy booleans
    if isinstance(obj, (np.bool_,)):
        return bool(obj)

    # Handle numpy integers
    if isinstance(obj, (np.integer,)):
        return int(obj)

    # Handle numpy floats
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        if math.isnan(v) or math.isinf(v):
            return None
        return v

    # Handle Python floats
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    # Everything else stays as-is
    return obj


@router.get("/{doc_id}/analytics")
async def get_document_analytics(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Get document-specific analytics for ANY document type

    Returns appropriate analytics based on document classification:
    - bank_statement → transaction analytics
    - invoice → line item analytics
    - receipt → purchase analytics
    - contract → clause analytics
    - spreadsheet → table analytics
    - tax_document → tax form analytics
    - financial_statement → financial metrics
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise Exception(status_code=404, detail="Document not found")

    if not doc.doc_metadata:
        raise Exception(status_code=404, detail="Document has no metadata")

    doc_type = doc.doc_type
    metadata = doc.doc_metadata

    # ✅ Route to appropriate analytics based on document type
    analytics_key = f"{doc_type}_analytics"

    if analytics_key not in metadata:
        raise Exception(
            status_code=404,
            detail=f"No specialized analytics found for {doc_type}. Available keys: {list(metadata.keys())}"
        )

    analytics = metadata[analytics_key]

    # ✅ Generate universal response structure
    response = {
        "document_id": doc_id,
        "filename": doc.filename,
        "document_type": doc_type,
        "analyzer_type": analytics.get("analyzer_type", doc_type),
        "analytics": analytics
    }

    # ✅ Add type-specific summary
    if doc_type == "bank_statement":
        response["summary"] = {
            "total_transactions": len(analytics.get("transactions", [])),
            "total_income": analytics.get("summary", {}).get("total_income", 0),
            "total_expenses": analytics.get("summary", {}).get("total_expenses", 0),
            "net_cash_flow": analytics.get("summary", {}).get("net_cash_flow", 0),
            "financial_health": analytics.get("financial_health", {}).get("status", "Unknown")
        }

    elif doc_type == "invoice":
        response["summary"] = {
            "invoice_number": analytics.get("invoice_details", {}).get("invoice_number", "N/A"),
            "total_amount": analytics.get("totals", {}).get("total", 0),
            "line_items": len(analytics.get("line_items", [])),
            "due_date": analytics.get("invoice_details", {}).get("due_date", "N/A")
        }

    elif doc_type == "receipt":
        response["summary"] = {
            "merchant": analytics.get("merchant_info", {}).get("name", "Unknown"),
            "total_amount": analytics.get("totals", {}).get("total", 0),
            "items": len(analytics.get("items", [])),
            "payment_method": analytics.get("payment_method", "Unknown")
        }

    elif doc_type == "contract":
        response["summary"] = {
            "contract_type": analytics.get("contract_type", "Unknown"),
            "parties": len(analytics.get("parties", [])),
            "clauses": len(analytics.get("clauses", []))
        }

    elif doc_type == "spreadsheet":
        tables = analytics.get("tables", [])
        response["summary"] = {
            "tables": len(tables),
            "total_rows": sum(t.get("rows", 0) for t in tables),
            "total_columns": sum(len(t.get("columns", [])) for t in tables)
        }

    elif doc_type == "tax_document":
        response["summary"] = {
            "form_type": analytics.get("form_type", "Unknown"),
            "tax_year": analytics.get("tax_year", "Unknown")
        }

    elif doc_type == "financial_statement":
        response["summary"] = {
            "statement_type": analytics.get("statement_type", "Unknown"),
            "period": analytics.get("period", "Unknown")
        }

    else:
        response["summary"] = {
            "message": f"Analytics available for {doc_type}"
        }

    print("SANITIZED JSON:", json.dumps(sanitize_for_json(response)))

    return sanitize_for_json(response)


@router.get("/{doc_id}/advanced-analytics/v2")
async def get_advanced_analytics(doc_id: str, db: Session = Depends(get_db)):
    # Get document
    document = db.query(Document).filter(Document.id == doc_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Load data based on file type
    df = await load_document_as_dataframe(document)

    if not isinstance(df, pd.DataFrame) or df.empty:
        raise HTTPException(
            status_code=422,
            detail=f"Unable to extract tabular data for {document.filename} document"
        )

    # Generate V2 dashboard analytics
    dashboard_data = generate_dashboard_analytics(df)
    ui_config = generate_ui_config_v2(dashboard_data)

    # Convert to JSON-safe format
    import json
    response_data = {
        "analyzed_data": {
            **dashboard_data,
            "ui_config": ui_config
        }
    }

    #json_str = json.dumps(response_data, default=str)
    logger.info("Did sanitized called")
    response = sanitize_for_json(response_data)
    return response


def clean_pdf_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # Drop empty rows/columns
    df = df.dropna(how="all")
    df = df.dropna(axis=1, how="all")

    # Clean column names
    df.columns = [
        str(c).strip() if c and str(c).strip() else f"Column_{i}"
        for i, c in enumerate(df.columns)
    ]

    # Remove duplicate header rows
    header = list(df.columns)
    df = df[~df.apply(lambda row: list(row.values) == header, axis=1)]

    # Clean numeric columns
    for col in df.columns:
        if df[col].dtype == object:
            cleaned = (
                df[col]
                .astype(str)
                .str.replace("$", "", regex=False)
                .str.replace(",", "", regex=False)
                .str.replace(" ", "", regex=False)  # <-- FIXES "$ 8 50,790"
                .str.replace("—", "", regex=False)
                .str.replace("-", "", regex=False)
            )

            numeric = pd.to_numeric(cleaned, errors="coerce")

            # Convert if majority numeric
            if numeric.notna().sum() > len(df) * 0.5:
                df[col] = numeric

    return df.reset_index(drop=True)


def dict_to_dataframe_loose(parsed: dict) -> pd.DataFrame:
    """
    Convert ANY dict (even with scalars, floats, uneven lists) into a clean DataFrame.
    """
    normalized = {}

    # Step 1: Convert all values to lists
    for key, value in parsed.items():
        if isinstance(value, list):
            normalized[key] = value
        else:
            # scalar → wrap in list
            normalized[key] = [value]

    # Step 2: Pad lists to same length
    max_len = max(len(v) for v in normalized.values())

    for key, values in normalized.items():
        if len(values) < max_len:
            normalized[key] = values + [None] * (max_len - len(values))

    return pd.DataFrame(normalized)


async def load_document_as_dataframe(document: Document) -> pd.DataFrame:
    """
    Load any document type into a pandas DataFrame for analytics
    """
    file_path = document.file_path
    #file_type = document.filename.lower()

    filename = document.filename.lower()  # Extract extension WITHOUT the dot
    file_type = os.path.splitext(filename)[1].lstrip(".")

    try:
        # Structured data files
        if file_type == 'csv':
            return pd.read_csv(file_path)

        elif file_type in ['xlsx', 'xls']:
            return pd.read_excel(file_path)

        elif file_type == 'json':
            df = pd.read_json(file_path)
            if any(isinstance(val, (dict, list)) for val in df.iloc[0] if val is not None):
                df = pd.json_normalize(df.to_dict('records'))
            return df

        elif file_type == 'parquet':
            return pd.read_parquet(file_path)

        # ✨ PDF - Extract from analyzed_data
        elif file_type == 'pdf':
            logger.info(f"📄 Processing PDF: {document.filename}")
            analyzed_data = document.doc_metadata["analyzed_data"]
            # Check analyzed_data for tables
            if analyzed_data and 'tables' in analyzed_data:
                tables = analyzed_data['tables']
                logger.info(f"📊 Found {len(tables)} table(s) in analyzed_data")

                if tables and len(tables) > 0:
                    # Get first table
                    table = tables[0]
                    logger.info(f"📋 Table format: {type(table)}")

                    # Normalize the table
                    from app.utils.table_utils import normalize_table, table_to_dataframe

                    normalized = normalize_table(table)

                    if normalized:
                        # Convert to DataFrame
                        df = table_to_dataframe(normalized)

                        if df is not None and len(df) > 0:
                            logger.info(f"✅ Successfully loaded DataFrame: {len(df)} rows × {len(df.columns)} columns")
                            logger.info(f"📋 Columns: {list(df.columns)}")
                            return df
                    else:
                        logger.warning("⚠️  Table normalization returned None")
            elif analyzed_data and 'tables' in analyzed_data["ui_config"]:
                tables = analyzed_data["ui_config"]["tables"]
                logger.info(f"📊 Found {len(tables)} table(s) in analyzed_data")

                if tables and len(tables) > 0:
                    # Get first table
                    table = tables[0]
                    logger.info(f"📋 Table format: {type(table)}")

                    # Normalize the table
                    from app.utils.table_utils import normalize_table, table_to_dataframe

                    normalized = normalize_table(table)

                    if normalized:
                        # Convert to DataFrame
                        df = table_to_dataframe(normalized)

                        if df is not None and len(df) > 0:
                            logger.info(
                                f"✅ Successfully loaded DataFrame: {len(df)} rows × {len(df.columns)} columns")
                            logger.info(f"📋 Columns: {list(df.columns)}")
                            return df
                    else:
                        logger.warning("⚠️  Table normalization returned None")

            # Fallback: re-parse PDF directly
            logger.info(f"🔄 Attempting direct PDF re-parse...")

            # Fallback: use parser.parse() which may return dict, list, or DataFrame
            logger.info("🔄 Attempting direct PDF re-parse using parser.parse()...")
            parser = UniversalParser()
            parsed = parser.parse(file_path, document.filename)

            df = None

            # 1️⃣ If parser returns a DataFrame
            if isinstance(parsed, pd.DataFrame):
                df = parsed

            # 2️⃣ If parser returns a dict (common for BankAnalyzer)
            elif isinstance(parsed, dict):
                try:
                    df = dict_to_dataframe_loose(parsed)
                    logger.info("📄 Converted dict → DataFrame (loose mode)")
                except Exception as e:
                    logger.error(f"❌ Failed converting dict to DataFrame: {e}")


            # 3️⃣ If parser returns list-of-lists (rare)
            elif isinstance(parsed, list):
                try:
                    df = pd.DataFrame(parsed)
                    logger.info("📄 Converted list → DataFrame")
                except Exception as e:
                    logger.error(f"❌ Failed converting list to DataFrame: {e}")

            # 4️⃣ If nothing usable returned
            if df is None:
                logger.warning("⚠️ parser.parse() returned unsupported format")
                return None

            # 5️⃣ Clean the DataFrame
            df = clean_pdf_dataframe(df)

            if df is not None and len(df) > 0:
                logger.info(f"✅ Direct parse successful: {len(df)} rows × {len(df.columns)} columns")
                return df

            logger.warning("⚠️ Direct parse returned empty DataFrame")
            return None

            #df = extract_tables_from_pdf_direct(file_path)

            #from app.parsers.pdf_parser import universal_parser
            # logger.info("🔄 Attempting UNIVERSAL PDF table extraction...")
            # df = parser.parse(file_path, document.filename)
            #
            # if df is not None and len(df) > 0:
            #     df = clean_pdf_dataframe(df)
            #     logger.info(f"✅ Direct parse successful: {len(df)} rows × {len(df.columns)} columns")
            #     return df

        # Other file types
        else:
            return extract_structured_data_from_analysis(document)

    except Exception as e:
        logger.error(f"❌ Error loading {file_type} as DataFrame: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def extract_tables_from_pdf_direct(file_path: str) -> Optional[pd.DataFrame]:
    """
    Re-parse PDF to extract tables directly
    """
    try:
        import pdfplumber

        logger.info(f"🔍 Opening PDF with pdfplumber: {file_path}")

        all_dataframes = []

        with pdfplumber.open(file_path) as pdf:
            logger.info(f"📄 PDF has {len(pdf.pages)} page(s)")

            for page_num, page in enumerate(pdf.pages, 1):
                tables = page.extract_tables()
                logger.info(f"📊 Page {page_num}: Found {len(tables) if tables else 0} table(s)")

                if tables:
                    for table_idx, table in enumerate(tables):
                        if table and len(table) > 1:
                            logger.info(
                                f"   Table {table_idx + 1}: {len(table)} rows × {len(table[0]) if table else 0} columns")

                            # Convert to DataFrame
                            headers = table[0]
                            data_rows = table[1:]

                            # Clean headers
                            clean_headers = []
                            for i, header in enumerate(headers):
                                if header and str(header).strip():
                                    clean_headers.append(str(header).strip())
                                else:
                                    clean_headers.append(f"Column_{i + 1}")

                            logger.info(f"   Headers: {clean_headers}")

                            # Create DataFrame
                            df = pd.DataFrame(data_rows, columns=clean_headers)

                            # Remove empty rows
                            df = df.dropna(how='all')

                            # Clean and convert numeric columns
                            for col in df.columns:
                                if df[col].dtype == 'object':
                                    df[col] = df[col].astype(str).str.strip()

                                    # Remove $ and commas
                                    cleaned = df[col].str.replace('$', '', regex=False)
                                    cleaned = cleaned.str.replace(',', '', regex=False)

                                    # Try numeric conversion
                                    numeric_series = pd.to_numeric(cleaned, errors='coerce')

                                    # Convert if mostly numeric
                                    non_null = df[col].notna().sum()
                                    if non_null > 0 and numeric_series.notna().sum() / non_null > 0.5:
                                        df[col] = numeric_series
                                        logger.info(f"   ✅ Converted '{col}' to numeric")

                            if len(df) > 0:
                                logger.info(f"   ✅ DataFrame created: {len(df)} rows")
                                all_dataframes.append(df)

        if all_dataframes:
            # Return the largest table
            largest_df = max(all_dataframes, key=len)
            logger.info(f"✅ Returning largest table: {len(largest_df)} rows × {len(largest_df.columns)} columns")
            return largest_df

        logger.warning("⚠️  No tables found in PDF")
        return None

    except Exception as e:
        logger.error(f"❌ Direct PDF extraction failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def extract_tables_from_pdf(file_path: str) -> Optional[pd.DataFrame]:
    """
    Extract tables from PDF using multiple methods
    """

    # Method 1: Try pdfplumber first (best for most PDFs)
    try:
        import pdfplumber

        all_tables = []

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()

                for table in tables:
                    if table and len(table) > 1:  # Must have headers + data
                        # Convert to DataFrame
                        df = pd.DataFrame(table[1:], columns=table[0])

                        # Clean column names
                        df.columns = [str(col).strip() if col else f"Column_{i}"
                                      for i, col in enumerate(df.columns)]

                        # Remove empty rows
                        df = df.dropna(how='all')

                        # Try to convert numeric columns
                        for col in df.columns:
                            # Remove currency symbols and commas
                            if df[col].dtype == 'object':
                                cleaned = df[col].str.replace('$', '').str.replace(',', '').str.strip()
                                try:
                                    df[col] = pd.to_numeric(cleaned, errors='ignore')
                                except:
                                    pass

                        all_tables.append(df)

        if all_tables:
            # If multiple tables, combine them
            if len(all_tables) == 1:
                return all_tables[0]
            else:
                # Try to concatenate if columns match
                try:
                    return pd.concat(all_tables, ignore_index=True)
                except:
                    # Return the largest table
                    return max(all_tables, key=len)

    except Exception as e:
        print(f"pdfplumber failed: {str(e)}")

    # Method 2: Try tabula-py (Java-based, good for complex PDFs)
    try:
        import tabula

        # Read all tables from PDF
        tables = tabula.read_pdf(file_path, pages='all', multiple_tables=True)

        if tables:
            # Clean and process tables
            cleaned_tables = []

            for df in tables:
                if len(df) > 0:
                    # Clean column names
                    df.columns = [str(col).strip() if col else f"Column_{i}"
                                  for i, col in enumerate(df.columns)]

                    # Remove empty rows
                    df = df.dropna(how='all')

                    # Convert numeric columns
                    for col in df.columns:
                        if df[col].dtype == 'object':
                            cleaned = df[col].astype(str).str.replace('$', '').str.replace(',', '').str.strip()
                            try:
                                df[col] = pd.to_numeric(cleaned, errors='ignore')
                            except:
                                pass

                    cleaned_tables.append(df)

            if cleaned_tables:
                if len(cleaned_tables) == 1:
                    return cleaned_tables[0]
                else:
                    # Return the largest table
                    return max(cleaned_tables, key=len)

    except Exception as e:
        print(f"tabula failed: {str(e)}")

    # Method 3: Fallback to analyzed_data
    return None


def extract_structured_data_from_analysis(document: Document) -> pd.DataFrame:
    """
    Extract structured/tabular data from the document's analyzed_data field
    """
    analyzed_data = document.doc_metadata["analyzed_data"]
    if not analyzed_data:
        return None

    data = analyzed_data
    rows = []

    # Try different extraction strategies based on document type

    # 1. Table data (from PDF or other sources)
    if 'table' in data or 'tables' in data:
        table_data = data.get('table') or data.get('tables')

        if isinstance(table_data, list) and len(table_data) > 0:
            # If it's a list of rows
            if isinstance(table_data[0], dict):
                return pd.DataFrame(table_data)
            elif isinstance(table_data[0], list):
                # First row might be headers
                if len(table_data) > 1:
                    return pd.DataFrame(table_data[1:], columns=table_data[0])

    # 2. Invoice/Receipt extraction
    if 'line_items' in data:
        for item in data['line_items']:
            rows.append(item)
        return pd.DataFrame(rows)

    # 3. Bank statement extraction
    if 'transactions' in data:
        for txn in data['transactions']:
            rows.append(txn)
        return pd.DataFrame(rows)

    # 4. Resume/CV extraction
    if 'experience' in data:
        for exp in data['experience']:
            rows.append(exp)
        if rows:
            return pd.DataFrame(rows)

    # 5. Contract/Legal document - extract key terms
    if 'key_terms' in data or 'clauses' in data:
        terms = data.get('key_terms', []) or data.get('clauses', [])
        for term in terms:
            if isinstance(term, dict):
                rows.append(term)
            else:
                rows.append({'term': term})
        if rows:
            return pd.DataFrame(rows)

    # 6. Generic extraction - flatten the entire analyzed_data
    if isinstance(data, dict):
        flat_data = flatten_dict(data)
        return pd.DataFrame([flat_data])

    return None


def flatten_dict(d: dict, parent_key: str = '', sep: str = '_') -> dict:
    """
    Flatten nested dictionary structure
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k

        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            if v and not isinstance(v[0], (dict, list)):
                items.append((new_key, ', '.join(map(str, v))))
            else:
                items.append((new_key, str(v)))
        else:
            items.append((new_key, v))

    return dict(items)


def extract_structured_data_from_analysis(document: Document) -> pd.DataFrame:
    """
    Extract structured/tabular data from the document's analyzed_data field
    This works for PDFs, invoices, contracts, resumes, etc.
    """
    if not document.doc_metadata["analyzed_data"]:
        return None

    data = document.doc_metadata["analyzed_data"]
    rows = []

    # Try different extraction strategies based on document type

    # 1. Invoice/Receipt extraction
    if 'line_items' in data:
        for item in data['line_items']:
            rows.append(item)
        return pd.DataFrame(rows)

    # 2. Bank statement extraction
    if 'transactions' in data:
        for txn in data['transactions']:
            rows.append(txn)
        return pd.DataFrame(rows)

    # 3. Resume/CV extraction
    if 'experience' in data:
        for exp in data['experience']:
            rows.append(exp)
        if rows:
            return pd.DataFrame(rows)

    # 4. Contract/Legal document - extract key terms
    if 'key_terms' in data or 'clauses' in data:
        terms = data.get('key_terms', []) or data.get('clauses', [])
        for term in terms:
            if isinstance(term, dict):
                rows.append(term)
            else:
                rows.append({'term': term})
        if rows:
            return pd.DataFrame(rows)

    # 5. Generic extraction - flatten the entire analyzed_data
    if isinstance(data, dict):
        # Convert nested dict to flat structure
        flat_data = flatten_dict(data)
        return pd.DataFrame([flat_data])

    return None


def flatten_dict(d: dict, parent_key: str = '', sep: str = '_') -> dict:
    """
    Flatten nested dictionary structure
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k

        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            # Handle lists of primitives
            if v and not isinstance(v[0], (dict, list)):
                items.append((new_key, ', '.join(map(str, v))))
            else:
                # Skip complex nested lists for now
                items.append((new_key, str(v)))
        else:
            items.append((new_key, v))

    return dict(items)


@router.get("/{doc_id}/advanced-analytics")
async def get_advanced_analytics(
        doc_id: str,
        db: Session = Depends(get_db)
):
    from fastapi import HTTPException

    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    meta = doc.doc_metadata or {}

    # The new unified analytics object
    analyzed_data = meta.get("analyzed_data") or {}

    logger.info(f"📊 Advanced analytics for {doc.doc_type} — keys: {list(analyzed_data.keys())}")

    response = {
        "document_id": str(doc.id),
        "filename": doc.filename,
        "document_type": doc.doc_type,
        "uploaded_at": doc.created_at.isoformat(),

        # Single source of truth: data + schema
        "analyzed_data": analyzed_data
    }

    return sanitize_for_json(response)

    # @router.get("/{doc_id}/advanced-analytics")


# async def get_advanced_analytics(
#         doc_id: str,
#         db: Session = Depends(get_db)
# ):
#     """
#     Get COMPLETE advanced analytics for ANY document type
#     Intelligently discovers and displays ALL available fields
#     """
#     try:
#         doc = db.query(Document).filter(Document.id == doc_id).first()
#
#         if not doc:
#             raise HTTPException(status_code=404, detail="Document not found")
#
#         if not doc.doc_metadata:
#             raise HTTPException(status_code=404, detail="No metadata available")
#
#         doc_type = doc.doc_type
#         logger.info(f"📊 Fetching advanced analytics for {doc_type}: {doc.filename}")
#         logger.info(f"📋 Available metadata keys: {list(doc.doc_metadata.keys())}")
#
#         # ✅ SMART ANALYTICS DISCOVERY
#         analytics = None
#         analytics_source = None
#
#         # Strategy 1: Try exact match
#         analytics_key = f"{doc_type}_analytics"
#         if analytics_key in doc.doc_metadata:
#             analytics = doc.doc_metadata[analytics_key]
#             analytics_source = analytics_key
#             logger.info(f"✅ Found analytics at: {analytics_key}")
#
#         # Strategy 2: Try any key ending with _analytics
#         if not analytics:
#             for key in doc.doc_metadata.keys():
#                 if key.endswith('_analytics'):
#                     analytics = doc.doc_metadata[key]
#                     analytics_source = key
#                     logger.info(f"✅ Found analytics at: {key}")
#                     break
#
#         # Strategy 3: Check for 'analyzed_data' (common key from pipeline)
#         if not analytics and 'analyzed_data' in doc.doc_metadata:
#             analytics = doc.doc_metadata['analyzed_data']
#             analytics_source = 'analyzed_data'
#             logger.info(f"✅ Found analytics at: analyzed_data")
#
#         # ⭐ Attach UI config if present
#         # Strategy 0: Direct UI config from pipeline
#         if "ui_config" in doc.doc_metadata["analyzed_data"]["ui_config"]:
#             ui_config = doc.doc_metadata["analyzed_data"]["ui_config"]
#             analytics_source = "ui_config"
#             logger.info("✅ Found analytics at: ui_config")
#
#
#
#         # Strategy 4: Look for tables, insights, keywords (structured data)
#         if not analytics:
#             # Build analytics from available structured data
#             temp_analytics = {}
#
#             if 'tables' in doc.doc_metadata:
#                 temp_analytics['tables'] = doc.doc_metadata['tables']
#             if 'insights' in doc.doc_metadata:
#                 temp_analytics['insights'] = doc.doc_metadata['insights']
#             if 'key_points' in doc.doc_metadata:
#                 temp_analytics['key_points'] = doc.doc_metadata['key_points']
#             if 'keywords' in doc.doc_metadata:
#                 temp_analytics['keywords'] = doc.doc_metadata['keywords']
#             if 'summary' in doc.doc_metadata:
#                 temp_analytics['summary'] = doc.doc_metadata['summary']
#
#             if temp_analytics:
#                 analytics = temp_analytics
#                 analytics_source = 'constructed_from_metadata'
#                 logger.info(f"✅ Constructed analytics from metadata fields: {list(temp_analytics.keys())}")
#
#         # Strategy 5: Use entire metadata as last resort
#         if not analytics:
#             analytics = doc.doc_metadata
#             analytics_source = 'full_metadata'
#             logger.info(f"⚠️ Using entire metadata as analytics")
#
#         if not analytics or (isinstance(analytics, dict) and len(analytics) == 0):
#             raise HTTPException(
#                 status_code=404,
#                 detail=f"No analytics data found for this document. Available keys: {list(doc.doc_metadata.keys())}"
#             )
#
#         # ✅ INTELLIGENT FIELD DISCOVERY
#         discovered_structure = _discover_analytics_structure(analytics)
#
#         # Build universal response
#         response = {
#             "document_id": doc_id,
#             "filename": doc.filename,
#             "document_type": doc_type,
#             "uploaded_at": doc.created_at.isoformat(),
#
#             # ✅ Intelligent structure discovery
#             "structure": discovered_structure,
#
#             # ✅ Complete raw data
#             "data": analytics,
#             "ui_config": ui_config,
#             # ✅ Metadata about the data
#             "metadata": {
#                 "total_fields": len(analytics) if isinstance(analytics, dict) else 0,
#                 "field_types": _analyze_field_types(analytics) if isinstance(analytics, dict) else {},
#                 "has_charts": "charts" in analytics if isinstance(analytics, dict) else False,
#                 "has_tables": any(isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict) for v in
#                                   analytics.values()) if isinstance(analytics, dict) else False,
#                 "has_insights": ("insights" in analytics or "recommendations" in analytics) if isinstance(analytics,
#                                                                                                           dict) else False,
#                 "analytics_source": analytics_source,
#                 "available_keys": list(doc.doc_metadata.keys())
#             },
#
#         }
#
#         logger.info(
#             f"✅ Advanced analytics generated: {len(discovered_structure.get('metrics', []))} metrics, {len(discovered_structure.get('tables', []))} tables, {len(discovered_structure.get('charts', []))} charts")
#
#         return response


# @router.get("/{doc_id}/advanced-analytics")
# async def get_advanced_analytics(
#         doc_id: str,
#         db: Session = Depends(get_db)
# ):
#     """
#     Get COMPLETE advanced analytics for ANY document type
#     Intelligently discovers and displays ALL available fields
#     """
#     try:
#         doc = db.query(Document).filter(Document.id == doc_id).first()
#
#         if not doc:
#             raise HTTPException(status_code=404, detail="Document not found")
#
#         if not doc.doc_metadata:
#             raise HTTPException(status_code=404, detail="No analytics available")
#
#         doc_type = doc.doc_type
#         analytics_key = f"{doc_type}_analytics"
#
#         if analytics_key not in doc.doc_metadata:
#             raise HTTPException(status_code=404, detail=f"No {doc_type} analytics found")
#
#         # Get the complete analytics
#         analytics = doc.doc_metadata[analytics_key]
#
#         # ✅ INTELLIGENT FIELD DISCOVERY
#         discovered_structure = _discover_analytics_structure(analytics)
#
#         # Build universal response
#         response = {
#             "document_id": doc_id,
#             "filename": doc.filename,
#             "document_type": doc_type,
#             "uploaded_at": doc.created_at.isoformat(),
#
#             # ✅ Intelligent structure discovery
#             "structure": discovered_structure,
#
#             # ✅ Complete raw data
#             "data": analytics,
#
#             # ✅ Metadata about the data
#             "metadata": {
#                 "total_fields": len(analytics),
#                 "field_types": _analyze_field_types(analytics),
#                 "has_charts": "charts" in analytics,
#                 "has_tables": any(
#                     isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict) for v in analytics.values()),
#                 "has_insights": "insights" in analytics or "recommendations" in analytics
#             }
#         }
#
#         return response
#
#     except Exception as e:
#         logger.error(f"❌ Advanced analytics error: {e}")
#         import traceback
#         traceback.print_exc()
#         raise HTTPException(status_code=500, detail=str(e))


def _discover_analytics_structure(analytics: Dict) -> Dict:
    """
    Intelligently discover the structure of analytics data
    Categorizes fields into: metrics, tables, charts, text, objects
    """
    structure = {
        "metrics": [],  # Single numeric values
        "summaries": [],  # Dict with multiple key-value pairs
        "tables": [],  # Lists of dicts (tabular data)
        "lists": [],  # Simple lists
        "charts": [],  # Chart data
        "text": [],  # String values
        "nested": []  # Complex nested structures
    }

    for key, value in analytics.items():
        field_info = {
            "key": key,
            "label": _humanize_key(key),
            "type": type(value).__name__,
            "sample": None
        }

        # Numeric metrics
        if isinstance(value, (int, float)):
            field_info["value"] = value
            structure["metrics"].append(field_info)

        # Tables (list of dicts)
        elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
            field_info["count"] = len(value)
            field_info["columns"] = list(value[0].keys()) if value else []
            field_info["sample"] = value[:3]  # First 3 rows
            structure["tables"].append(field_info)

        # Simple lists
        elif isinstance(value, list):
            field_info["count"] = len(value)
            field_info["sample"] = value[:5]
            structure["lists"].append(field_info)

        # Summary objects (dicts with metrics)
        elif isinstance(value, dict) and all(isinstance(v, (int, float, str)) for v in value.values()):
            field_info["fields"] = list(value.keys())
            field_info["data"] = value
            structure["summaries"].append(field_info)

        # Charts
        elif "chart" in key.lower() or key in ["visualizations", "graphs"]:
            field_info["data"] = value
            structure["charts"].append(field_info)

        # Text fields
        elif isinstance(value, str):
            field_info["value"] = value
            field_info["length"] = len(value)
            structure["text"].append(field_info)

        # Complex nested structures
        elif isinstance(value, dict):
            field_info["keys"] = list(value.keys())
            field_info["data"] = value
            structure["nested"].append(field_info)

    return structure


def _analyze_field_types(analytics: Dict) -> Dict:
    """Analyze and count field types"""
    type_counts = {}

    for value in analytics.values():
        type_name = type(value).__name__
        type_counts[type_name] = type_counts.get(type_name, 0) + 1

    return type_counts


def _humanize_key(key: str) -> str:
    """Convert snake_case to Title Case"""
    return key.replace('_', ' ').title()


@router.get("/{doc_id}/advanced")
async def get_advanced_analytics(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Get COMPLETE advanced analytics for ANY document type

    Automatically adapts to show relevant data based on document type:
    - bank_statement → financial analytics
    - invoice → line items, totals, vendor details
    - receipt → items, merchant, payment
    - contract → parties, clauses, obligations
    - spreadsheet → tables, columns, rows
    - tax_document → forms, deductions, refunds
    - financial_statement → balance sheet, income statement
    """
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()

        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        if not doc.doc_metadata:
            raise HTTPException(status_code=404, detail="No analytics available")

        doc_type = doc.doc_type
        analytics_key = f"{doc_type}_analytics"

        if analytics_key not in doc.doc_metadata:
            raise HTTPException(status_code=404, detail=f"No {doc_type} analytics found")

        # Get the complete analytics
        analytics = doc.doc_metadata[analytics_key]

        # ===== UNIVERSAL RESPONSE STRUCTURE =====
        response = {
            "document_id": doc_id,
            "filename": doc.filename,
            "document_type": doc_type,
            "uploaded_at": doc.created_at.isoformat(),
            "analyzer_type": analytics.get("analyzer_type", doc_type)
        }

        # ===== ROUTE TO DOCUMENT-SPECIFIC HANDLER =====
        if doc_type == "bank_statement":
            response.update(_format_bank_statement_analytics(analytics))

        elif doc_type == "invoice":
            response.update(_format_invoice_analytics(analytics))

        elif doc_type == "receipt":
            response.update(_format_receipt_analytics(analytics))

        elif doc_type == "contract":
            response.update(_format_contract_analytics(analytics))

        elif doc_type == "spreadsheet":
            response.update(_format_spreadsheet_analytics(analytics))

        elif doc_type == "tax_document":
            response.update(_format_tax_document_analytics(analytics))

        elif doc_type == "financial_statement":
            response.update(_format_financial_statement_analytics(analytics))

        else:
            # Generic fallback - just return everything
            response.update({
                "overview": {"message": f"Advanced analytics for {doc_type}"},
                "data": analytics,
                "raw_analytics": analytics
            })

        return response

    except Exception as e:
        logger.error(f"❌ Advanced analytics error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ===== BANK STATEMENT FORMATTER =====
def _format_bank_statement_analytics(analytics: Dict) -> Dict:
    """Format bank statement analytics"""
    return {
        "overview": {
            "account_holder": analytics.get("account_information", {}).get("account_holder", "N/A"),
            "account_number": analytics.get("account_information", {}).get("account_number", "N/A"),
            "statement_period": analytics.get("statement_period", {}),
            "balances": analytics.get("balances", {}),
            "total_transactions": len(analytics.get("transactions", [])),
            "financial_health_score": analytics.get("financial_health", {}).get("score", 0),
            "financial_health_status": analytics.get("financial_health", {}).get("status", "Unknown")
        },

        "financial_summary": {
            "transaction_summary": analytics.get("transaction_summary", {}),
            "income_analysis": analytics.get("income_analysis", {}),
            "spending_analysis": analytics.get("spending_analysis", {}),
            "savings_analysis": analytics.get("savings_analysis", {})
        },

        "income": {
            "total_income": analytics.get("income_analysis", {}).get("total_income", 0),
            "income_sources": analytics.get("income_sources", []),
            "top_deposits": analytics.get("top_deposits", [])
        },

        "spending": {
            "total_spending": analytics.get("spending_analysis", {}).get("total_spending", 0),
            "spending_by_category": analytics.get("spending_by_category", {}),
            "top_categories": analytics.get("top_categories", []),
            "top_withdrawals": analytics.get("top_withdrawals", []),
            "top_merchants": analytics.get("top_merchants", [])
        },

        "recurring": {
            "recurring_transactions": analytics.get("recurring_transactions", []),
            "recurring_bills": analytics.get("recurring_bills", []),
            "subscription_services": analytics.get("subscription_services", [])
        },

        "alerts": {
            "anomalies": analytics.get("anomalies", []),
            "fraud_alerts": analytics.get("fraud_alerts", []),
            "unusual_patterns": analytics.get("unusual_patterns", [])
        },

        "health": {
            "score": analytics.get("financial_health", {}).get("score", 0),
            "status": analytics.get("financial_health", {}).get("status", "Unknown"),
            "factors": analytics.get("financial_health", {}).get("factors", []),
            "recommendations": analytics.get("recommendations", [])
        },

        "cash_flow": analytics.get("cash_flow", {}),

        "visualizations": {
            "charts": analytics.get("charts", {}),
            "available_charts": ["spending_by_category", "income_vs_expenses", "daily_spending", "cash_flow"]
        },

        "insights": analytics.get("insights", []),
        "transactions": analytics.get("transactions", []),
        "raw_analytics": analytics
    }


# ===== INVOICE FORMATTER =====
def _format_invoice_analytics(analytics: Dict) -> Dict:
    """Format invoice analytics"""
    return {
        "overview": {
            "invoice_number": analytics.get("invoice_number", "N/A"),
            "invoice_date": analytics.get("invoice_date", "N/A"),
            "due_date": analytics.get("due_date", "N/A"),
            "status": analytics.get("status", "Unknown"),
            "total_amount": analytics.get("totals", {}).get("total", 0)
        },

        "parties": {
            "vendor": analytics.get("vendor", {}),
            "customer": analytics.get("customer", {}),
            "billing_address": analytics.get("billing_address", {}),
            "shipping_address": analytics.get("shipping_address", {})
        },

        "line_items": {
            "items": analytics.get("line_items", []),
            "total_items": len(analytics.get("line_items", [])),
            "categories": list(set(item.get("category", "Other") for item in analytics.get("line_items", [])))
        },

        "financials": {
            "subtotal": analytics.get("totals", {}).get("subtotal", 0),
            "tax": analytics.get("totals", {}).get("tax", 0),
            "total": analytics.get("totals", {}).get("total", 0),
            "amount_paid": analytics.get("totals", {}).get("amount_paid", 0),
            "balance_due": analytics.get("totals", {}).get("balance_due", 0)
        },

        "payment": {
            "payment_terms": analytics.get("payment_terms", "N/A"),
            "payment_method": analytics.get("payment_method", "N/A"),
            "payment_status": analytics.get("payment_status", "Unknown")
        },

        "insights": analytics.get("insights", []),
        "raw_analytics": analytics
    }


# ===== RECEIPT FORMATTER =====
def _safe_get(data: any, *keys, default=None):
    """Safely get nested dictionary values"""
    try:
        result = data
        for key in keys:
            if isinstance(result, dict):
                result = result.get(key, default)
            else:
                return default
        return result if result is not None else default
    except:
        return default


# ===== RECEIPT FORMATTER (Improved) =====
def _format_receipt_analytics(analytics: Dict) -> Dict:
    """Format receipt analytics with safe data access"""
    try:
        # Safely get items
        items_data = analytics.get("items", [])
        if isinstance(items_data, list):
            purchased_items = items_data
        else:
            purchased_items = items_data.get("purchased_items", [])

        total_items = len(purchased_items)
        categories = []
        try:
            categories = list(set(
                item.get("category", "Other")
                for item in purchased_items
                if isinstance(item, dict) and "category" in item
            ))
        except:
            pass

        return {
            "overview": {
                "merchant": _safe_get(analytics, "merchant_info", "name", default="Unknown"),
                "location": _safe_get(analytics, "merchant_info", "location", default="N/A"),
                "date": analytics.get("purchase_date", "N/A"),
                "total_amount": _safe_get(analytics, "totals", "total", default=0)
            },

            "merchant": analytics.get("merchant_info", {}),

            "items": {
                "purchased_items": purchased_items,
                "total_items": total_items,
                "categories": categories
            },

            "financials": {
                "subtotal": _safe_get(analytics, "totals", "subtotal", default=0),
                "tax": _safe_get(analytics, "totals", "tax", default=0),
                "tip": _safe_get(analytics, "totals", "tip", default=0),
                "total": _safe_get(analytics, "totals", "total", default=0)
            },

            "payment": {
                "payment_method": analytics.get("payment_method", "N/A"),
                "card_last_4": analytics.get("card_last_4", None)
            },

            "insights": analytics.get("insights", []),
            "raw_analytics": analytics
        }
    except Exception as e:
        logger.error(f"Error formatting receipt analytics: {e}")
        return {
            "overview": {},
            "merchant": {},
            "items": {"purchased_items": [], "total_items": 0, "categories": []},
            "financials": {},
            "payment": {},
            "insights": [],
            "raw_analytics": analytics
        }


# def _format_receipt_analytics(analytics: Dict) -> Dict:
#     """Format receipt analytics"""
#     return {
#         "overview": {
#             "merchant": analytics.get("merchant_info", {}).get("name", "Unknown"),
#             "location": analytics.get("merchant_info", {}).get("location", "N/A"),
#             "date": analytics.get("purchase_date", "N/A"),
#             "total_amount": analytics.get("totals", {}).get("total", 0)
#         },
#
#         "merchant": analytics.get("merchant_info", {}),
#
#         "items": {
#             "purchased_items": analytics.get("items", []),
#             "total_items": len(analytics.get("items", [])),
#             "categories": list(set(item.get("category", "Other") for item in analytics.get("items", [])))
#         },
#
#         "financials": {
#             "subtotal": analytics.get("totals", {}).get("subtotal", 0),
#             "tax": analytics.get("totals", {}).get("tax", 0),
#             "tip": analytics.get("totals", {}).get("tip", 0),
#             "total": analytics.get("totals", {}).get("total", 0)
#         },
#
#         "payment": {
#             "payment_method": analytics.get("payment_method", "N/A"),
#             "card_last_4": analytics.get("card_last_4", None)
#         },
#
#         "insights": analytics.get("insights", []),
#         "raw_analytics": analytics
#     }


# ===== CONTRACT FORMATTER =====
def _format_contract_analytics(analytics: Dict) -> Dict:
    """Format contract analytics"""
    return {
        "overview": {
            "contract_type": analytics.get("contract_type", "Unknown"),
            "effective_date": analytics.get("effective_date", "N/A"),
            "expiration_date": analytics.get("expiration_date", "N/A"),
            "status": analytics.get("status", "Unknown"),
            "total_clauses": len(analytics.get("clauses", []))
        },

        "parties": {
            "all_parties": analytics.get("parties", []),
            "party_count": len(analytics.get("parties", []))
        },

        "terms": {
            "duration": analytics.get("duration", "N/A"),
            "renewal_terms": analytics.get("renewal_terms", "N/A"),
            "termination_clause": analytics.get("termination_clause", "N/A")
        },

        "clauses": {
            "all_clauses": analytics.get("clauses", []),
            "by_category": _group_by_key(analytics.get("clauses", []), "category")
        },

        "obligations": {
            "party_obligations": analytics.get("obligations", []),
            "deliverables": analytics.get("deliverables", [])
        },

        "financials": {
            "contract_value": analytics.get("contract_value", 0),
            "payment_terms": analytics.get("payment_terms", "N/A"),
            "penalties": analytics.get("penalties", [])
        },

        "risk_analysis": {
            "risk_factors": analytics.get("risk_factors", []),
            "compliance_requirements": analytics.get("compliance_requirements", [])
        },

        "insights": analytics.get("insights", []),
        "raw_analytics": analytics
    }


# ===== SPREADSHEET FORMATTER =====
def _format_spreadsheet_analytics(analytics: Dict) -> Dict:
    """Format spreadsheet analytics"""
    tables = analytics.get("tables", [])

    return {
        "overview": {
            "total_tables": len(tables),
            "total_rows": sum(t.get("rows", 0) for t in tables),
            "total_columns": sum(len(t.get("columns", [])) for t in tables)
        },

        "tables": [
            {
                "table_name": t.get("name", f"Table {i + 1}"),
                "rows": t.get("rows", 0),
                "columns": t.get("columns", []),
                "column_count": len(t.get("columns", [])),
                "data_types": t.get("data_types", {}),
                "summary_stats": t.get("summary_stats", {})
            }
            for i, t in enumerate(tables)
        ],

        "data_quality": {
            "completeness": analytics.get("data_quality", {}).get("completeness", 0),
            "duplicates": analytics.get("data_quality", {}).get("duplicates", 0),
            "missing_values": analytics.get("data_quality", {}).get("missing_values", 0)
        },

        "insights": analytics.get("insights", []),
        "raw_analytics": analytics
    }


# ===== TAX DOCUMENT FORMATTER =====
def _format_tax_document_analytics(analytics: Dict) -> Dict:
    """Format tax document analytics"""
    return {
        "overview": {
            "form_type": analytics.get("form_type", "Unknown"),
            "tax_year": analytics.get("tax_year", "N/A"),
            "filing_status": analytics.get("filing_status", "N/A")
        },

        "taxpayer": {
            "name": analytics.get("taxpayer_info", {}).get("name", "N/A"),
            "ssn": analytics.get("taxpayer_info", {}).get("ssn_masked", "XXX-XX-XXXX"),
            "address": analytics.get("taxpayer_info", {}).get("address", {})
        },

        "income": {
            "total_income": analytics.get("income", {}).get("total", 0),
            "sources": analytics.get("income", {}).get("sources", [])
        },

        "deductions": {
            "total_deductions": analytics.get("deductions", {}).get("total", 0),
            "itemized_deductions": analytics.get("deductions", {}).get("itemized", []),
            "standard_deduction": analytics.get("deductions", {}).get("standard", 0)
        },

        "tax_summary": {
            "total_tax": analytics.get("tax_summary", {}).get("total_tax", 0),
            "tax_withheld": analytics.get("tax_summary", {}).get("tax_withheld", 0),
            "refund": analytics.get("tax_summary", {}).get("refund", 0),
            "amount_owed": analytics.get("tax_summary", {}).get("amount_owed", 0)
        },

        "insights": analytics.get("insights", []),
        "raw_analytics": analytics
    }


# ===== FINANCIAL STATEMENT FORMATTER =====
def _format_financial_statement_analytics(analytics: Dict) -> Dict:
    """Format financial statement analytics"""
    return {
        "overview": {
            "statement_type": analytics.get("statement_type", "Unknown"),
            "period": analytics.get("period", "N/A"),
            "company": analytics.get("company_info", {}).get("name", "N/A")
        },

        "balance_sheet": analytics.get("balance_sheet", {}),
        "income_statement": analytics.get("income_statement", {}),
        "cash_flow_statement": analytics.get("cash_flow_statement", {}),

        "key_metrics": {
            "total_assets": analytics.get("key_metrics", {}).get("total_assets", 0),
            "total_liabilities": analytics.get("key_metrics", {}).get("total_liabilities", 0),
            "equity": analytics.get("key_metrics", {}).get("equity", 0),
            "revenue": analytics.get("key_metrics", {}).get("revenue", 0),
            "net_income": analytics.get("key_metrics", {}).get("net_income", 0)
        },

        "ratios": analytics.get("financial_ratios", {}),

        "insights": analytics.get("insights", []),
        "raw_analytics": analytics
    }


# ===== HELPER FUNCTIONS =====
def _group_by_key(items: List[Dict], key: str) -> Dict:
    """Group list of dicts by a key"""
    grouped = {}
    for item in items:
        category = item.get(key, "Other")
        if category not in grouped:
            grouped[category] = []
        grouped[category].append(item)
    return grouped
