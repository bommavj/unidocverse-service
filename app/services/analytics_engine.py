"""
Universal Dynamic Analytics Engine
Works with ANY document structure - no hardcoding
"""
from http.client import HTTPException

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional

from fastapi import Depends
from sqlalchemy.orm import Session
import logging

from app.core.database import get_db
from app.models.document import Document

logger = logging.getLogger(__name__)


class DataSchemaAnalyzer:
    """Automatically detect schema and data types from any dataset"""

    @staticmethod
    def analyze_dataframe(df: pd.DataFrame) -> Dict[str, Any]:
        """
        Analyze dataframe structure and infer semantic meaning
        Returns schema with column types, suggested aggregations, etc.
        """

        schema = {
            "columns": [],
            "row_count": len(df),
            "numeric_columns": [],
            "categorical_columns": [],
            "date_columns": [],
            "text_columns": [],
            "id_columns": [],
            "suggested_aggregations": [],
            "suggested_groupings": []
        }

        for col in df.columns:
            col_info = {
                "name": col,
                "dtype": str(df[col].dtype),
                "null_count": int(df[col].isnull().sum()),
                "unique_count": int(df[col].nunique()),
                "sample_values": df[col].dropna().head(3).tolist()
            }

            # Detect column semantic type
            col_lower = col.lower()

            # Numeric/Currency detection
            if pd.api.types.is_numeric_dtype(df[col]):
                col_info["semantic_type"] = "numeric"
                schema["numeric_columns"].append(col)

                # Check if it's currency
                if any(keyword in col_lower for keyword in ['price', 'cost', 'revenue', 'amount', 'total', 'payment']):
                    col_info["semantic_type"] = "currency"
                    schema["suggested_aggregations"].append({
                        "column": col,
                        "type": "sum",
                        "label": f"Total {col}"
                    })

                # Check if it's a count/quantity
                elif any(keyword in col_lower for keyword in ['quantity', 'count', 'number', 'qty']):
                    col_info["semantic_type"] = "quantity"
                    schema["suggested_aggregations"].append({
                        "column": col,
                        "type": "sum",
                        "label": f"Total {col}"
                    })

            # Date detection
            elif pd.api.types.is_datetime64_any_dtype(df[col]) or \
                 any(keyword in col_lower for keyword in ['date', 'time', 'when', 'created', 'updated']):
                col_info["semantic_type"] = "date"
                schema["date_columns"].append(col)

            # ID detection
            elif any(keyword in col_lower for keyword in ['id', 'code', 'number', 'ref']) and \
                 df[col].nunique() / len(df) > 0.9:  # High uniqueness
                col_info["semantic_type"] = "id"
                schema["id_columns"].append(col)

            # Categorical detection (good for grouping)
            elif df[col].nunique() < len(df) * 0.5 and df[col].nunique() < 50:
                col_info["semantic_type"] = "categorical"
                schema["categorical_columns"].append(col)
                schema["suggested_groupings"].append({
                    "column": col,
                    "label": f"By {col}",
                    "unique_values": df[col].unique().tolist()[:20]  # Limit to 20
                })

            # Text detection
            else:
                col_info["semantic_type"] = "text"
                schema["text_columns"].append(col)

            schema["columns"].append(col_info)

        return schema


class NaturalLanguageQueryEngine:
    """Convert natural language to data queries"""

    def parse_query(self, query: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse natural language query into structured query
        Simple rule-based approach (no AI needed for basic queries)
        """

        query_lower = query.lower()

        result = {
            "intent": "filter",
            "filters": [],
            "group_by": [],
            "aggregations": [],
            "order_by": None,
            "limit": None
        }

        # Detect aggregation intent
        if any(word in query_lower for word in ['total', 'sum', 'revenue']):
            result["intent"] = "aggregate"
        elif 'top' in query_lower:
            result["intent"] = "top"
            import re
            match = re.search(r'top\s+(\d+)', query_lower)
            if match:
                result["limit"] = int(match.group(1))
        elif any(word in query_lower for word in ['how many', 'count']):
            result["intent"] = "count"

        # Detect grouping
        for col_info in schema['columns']:
            col_name = col_info['name']
            if f"by {col_name.lower()}" in query_lower:
                result["group_by"].append(col_name)

        # Detect filters (look for values in categorical columns)
        for col_info in schema['columns']:
            if col_info['semantic_type'] in ['categorical', 'text']:
                for value in col_info.get('sample_values', []):
                    if str(value).lower() in query_lower:
                        result["filters"].append({
                            "column": col_info['name'],
                            "operator": "equals",
                            "value": value
                        })

        # Auto-detect aggregation columns
        for col_name in schema['numeric_columns']:
            if any(word in col_name.lower() for word in ['price', 'revenue', 'amount', 'total']):
                result["aggregations"].append({
                    "column": col_name,
                    "function": "sum"
                })
                break

        return result


class UniversalAnalyticsEngine:
    """Main analytics engine - works with any data"""

    def __init__(self):
        self.schema_analyzer = DataSchemaAnalyzer()
        self.query_engine = NaturalLanguageQueryEngine()

    # def get_document_data(self, db: Session, doc_id: Optional[str] = None) -> pd.DataFrame:
    #     """Extract data from document(s) - with multiple fallback strategies"""
    #
    #     if doc_id:
    #         doc = db.query(Document).filter(Document.id == doc_id).first()
    #         if not doc:
    #             raise ValueError(f"Document {doc_id} not found")
    #         docs = [doc]
    #     else:
    #         # Get all spreadsheet documents
    #         docs = db.query(Document).filter(
    #             Document.doc_type.in_(['spreadsheet', 'sales_data', 'financial_report'])
    #         ).all()
    #
    #         if not docs:
    #             # Try all completed documents
    #             docs = db.query(Document).filter(
    #                 Document.status == 'completed'
    #             ).all()
    #
    #     if not docs:
    #         raise ValueError("No data documents found. Please upload a spreadsheet document.")
    #
    #     all_data = []
    #
    #     for doc in docs:
    #         metadata = doc.doc_metadata or {}
    #
    #         # Strategy 1: Try to get from 'tables' metadata
    #         tables = metadata.get('tables', [])
    #
    #         if tables:
    #             logger.info(f"✅ Found {len(tables)} table(s) in {doc.filename}")
    #
    #             for idx, table in enumerate(tables):
    #                 data = table.get('data', [])
    #                 headers = table.get('headers', None)
    #
    #                 logger.info(f"   Table {idx + 1}:")
    #                 logger.info(f"     - Headers: {headers}")
    #                 logger.info(f"     - Data rows: {len(data)}")
    #
    #                 if not data or len(data) == 0:
    #                     logger.warning(f"     ⚠️ Data is empty for table {idx + 1}")
    #                     continue
    #
    #                 # ✅ FIX: If headers are None/empty, use first row as headers
    #                 if not headers or headers is None:
    #                     logger.warning(f"     ⚠️ Headers are None/empty, using first row as headers")
    #
    #                     if len(data) > 0:
    #                         headers = data[0]  # First row becomes headers
    #                         data = data[1:]  # Rest is data
    #
    #                         logger.info(f"     ✅ Extracted headers from first row: {headers}")
    #                         logger.info(f"     ✅ Remaining data rows: {len(data)}")
    #                     else:
    #                         logger.error(f"     ❌ No data to extract headers from")
    #                         continue
    #
    #                 # ✅ Ensure headers is a list
    #                 if not isinstance(headers, list):
    #                     logger.error(f"     ❌ Headers is not a list: {type(headers)}")
    #                     continue
    #
    #                 # ✅ Ensure data is a list
    #                 if not isinstance(data, list):
    #                     logger.error(f"     ❌ Data is not a list: {type(data)}")
    #                     continue
    #
    #                 try:
    #                     df = pd.DataFrame(data, columns=headers)
    #                     all_data.append(df)
    #                     logger.info(f"     ✅ Successfully created DataFrame: {len(df)} rows × {len(df.columns)} cols")
    #                     logger.info(f"     ✅ Columns: {df.columns.tolist()}")
    #                 except Exception as e:
    #                     logger.error(f"     ❌ Failed to create DataFrame: {e}")
    #                     logger.error(f"     Headers: {headers}")
    #                     logger.error(f"     Data sample: {data[0] if data else 'None'}")
    #                     continue
    #
    #         # Strategy 2: Try to parse from raw_text if it looks like CSV/TSV
    #         elif doc.raw_text and ('\t' in doc.raw_text or ',' in doc.raw_text):
    #             logger.info(f"⚠️ No tables metadata, trying to parse raw_text for {doc.filename}")
    #
    #             try:
    #                 from io import StringIO
    #
    #                 # Detect delimiter
    #                 delimiter = '\t' if '\t' in doc.raw_text else ','
    #
    #                 # Try to read as CSV
    #                 df = pd.read_csv(StringIO(doc.raw_text), sep=delimiter, on_bad_lines='skip')
    #
    #                 if len(df) > 0:
    #                     all_data.append(df)
    #                     logger.info(f"✅ Parsed from raw_text: {len(df)} rows, {len(df.columns)} columns")
    #
    #             except Exception as e:
    #                 logger.warning(f"Could not parse raw_text as CSV: {e}")
    #
    #     if not all_data:
    #         raise ValueError(
    #             f"No table data found in documents. "
    #             f"Checked {len(docs)} document(s). "
    #             f"Tables metadata exists but data/headers are empty or invalid."
    #         )
    #
    #     # Combine all tables
    #     combined_df = pd.concat(all_data, ignore_index=True)
    #
    #     # Clean column names
    #     combined_df.columns = [str(col).strip() for col in combined_df.columns]
    #
    #     logger.info(f"📊 Final combined data: {len(combined_df)} rows, {len(combined_df.columns)} columns")
    #     logger.info(f"   Columns: {combined_df.columns.tolist()}")
    #
    #     return combined_df

    def get_document_data(self, db: Session, doc_id: str) -> pd.DataFrame:
        """
        Extract tabular data from document - supports both tables and analyzer results
        """
        logger.info(f"📊 Extracting data from document: {doc_id}")

        doc = db.query(Document).filter(Document.id == doc_id).first()

        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        if not doc.doc_metadata:
            raise ValueError(f"Document {doc_id} has no metadata")

        # ✅ OPTION 1: Check for analyzer-specific data (bank statements, invoices, etc.)
        analyzer_data = self._extract_analyzer_data(doc)
        if analyzer_data is not None:
            logger.info(f"✅ Using analyzer-specific data")
            return analyzer_data

        # ✅ OPTION 2: Check for regular table data
        tables = doc.doc_metadata.get("tables", [])

        if not tables:
            raise ValueError(f"No table or analyzer data found in document {doc_id}")

        # Find valid table
        for i, table in enumerate(tables):
            if not isinstance(table, dict):
                continue

            columns = table.get("columns") or table.get("headers")
            data = table.get("data") or table.get("rows")

            if not columns or not data:
                continue

            try:
                # Create DataFrame
                if isinstance(data[0], dict):
                    df = pd.DataFrame(data)
                else:
                    df = pd.DataFrame(data[1:], columns=columns)

                if not df.empty:
                    logger.info(f"✅ Created DataFrame from table {i}: {len(df)} rows × {len(df.columns)} cols")
                    return df

            except Exception as e:
                logger.warning(f"Failed to create DataFrame from table {i}: {e}")
                continue

        raise ValueError(
            f"No valid table data found in document. "
            f"Checked {len(tables)} table(s). "
            f"Tables exist but data/headers are empty or invalid."
        )

    def _extract_analyzer_data(self, doc: Document) -> Optional[pd.DataFrame]:
        """
        Extract data from document-specific analyzers

        Returns:
            DataFrame if analyzer data found, None otherwise
        """
        metadata = doc.doc_metadata

        # ✅ Check for bank statement analytics
        if "bank_statement_analytics" in metadata:
            return self._extract_bank_statement_data(metadata["bank_statement_analytics"])

        # ✅ Check for invoice analytics
        if "invoice_analytics" in metadata:
            return self._extract_invoice_data(metadata["invoice_analytics"])

        # ✅ Check for receipt analytics
        if "receipt_analytics" in metadata:
            return self._extract_receipt_data(metadata["receipt_analytics"])

        # ✅ Check for spreadsheet data
        if "spreadsheet_analytics" in metadata:
            return self._extract_spreadsheet_data(metadata["spreadsheet_analytics"])

        return None

    def _extract_bank_statement_data(self, analytics: Dict) -> pd.DataFrame:
        """Convert bank statement transactions to DataFrame"""
        logger.info(f"💰 Extracting bank statement transaction data")

        transactions = analytics.get("transactions", [])

        if not transactions:
            logger.warning("No transactions found in bank statement")
            return None

        # Convert to DataFrame
        df = pd.DataFrame(transactions)

        # Ensure proper columns
        required_cols = ['date', 'description', 'amount', 'debit', 'credit']
        for col in required_cols:
            if col not in df.columns:
                df[col] = 0.0 if col in ['amount', 'debit', 'credit'] else ''

        # Convert date to datetime
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')

        logger.info(f"✅ Extracted {len(df)} transactions")

        return df

    def _extract_invoice_data(self, analytics: Dict) -> pd.DataFrame:
        """Convert invoice line items to DataFrame"""
        logger.info(f"🧾 Extracting invoice line items")

        line_items = analytics.get("line_items", [])

        if not line_items:
            logger.warning("No line items found in invoice")
            return None

        # Convert to DataFrame
        if isinstance(line_items[0], dict):
            df = pd.DataFrame(line_items)
        else:
            # Assume first row is headers
            df = pd.DataFrame(line_items[1:], columns=line_items[0])

        logger.info(f"✅ Extracted {len(df)} line items")

        return df

    def _extract_receipt_data(self, analytics: Dict) -> pd.DataFrame:
        """Convert receipt items to DataFrame"""
        logger.info(f"🧾 Extracting receipt items")

        items = analytics.get("items", [])

        if not items:
            logger.warning("No items found in receipt")
            return None

        # Convert to DataFrame
        if isinstance(items[0], dict):
            df = pd.DataFrame(items)
        else:
            df = pd.DataFrame(items[1:], columns=items[0])

        logger.info(f"✅ Extracted {len(df)} items")

        return df

    def _extract_spreadsheet_data(self, analytics: Dict) -> pd.DataFrame:
        """Extract data from spreadsheet analytics"""
        logger.info(f"📊 Extracting spreadsheet data")

        tables = analytics.get("tables", [])

        if not tables:
            logger.warning("No tables found in spreadsheet")
            return None

        # Use first table
        table = tables[0]
        columns = table.get("columns", [])
        data = table.get("data", [])

        if not data:
            return None

        # Create DataFrame
        if isinstance(data[0], dict):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame(data[1:], columns=columns)

        logger.info(f"✅ Extracted {len(df)} rows")

        return df

    def analyze_schema(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze dataframe and return schema"""
        return self.schema_analyzer.analyze_dataframe(df)

    def execute_query(self, df: pd.DataFrame, parsed_query: Dict) -> pd.DataFrame:
        """Execute parsed query on dataframe"""

        result_df = df.copy()

        # Apply filters
        for filter_spec in parsed_query.get('filters', []):
            col = filter_spec['column']
            op = filter_spec['operator']
            val = filter_spec['value']

            if col not in result_df.columns:
                continue

            if op == 'equals':
                result_df = result_df[result_df[col].astype(str).str.lower() == str(val).lower()]
            elif op == 'contains':
                result_df = result_df[result_df[col].astype(str).str.contains(str(val), case=False, na=False)]
            elif op == 'greater':
                result_df = result_df[pd.to_numeric(result_df[col], errors='coerce') > float(val)]

        # Apply grouping and aggregation
        if parsed_query.get('group_by'):
            agg_dict = {}

            for agg_spec in parsed_query.get('aggregations', []):
                col = agg_spec['column']
                func = agg_spec['function']

                if col in result_df.columns:
                    agg_dict[col] = func

            if not agg_dict:
                # Default: count
                agg_dict = {result_df.columns[0]: 'count'}

            result_df = result_df.groupby(parsed_query['group_by']).agg(agg_dict).reset_index()

        elif parsed_query.get('intent') == 'count':
            # Just count filtered rows
            count = len(result_df)
            result_df = pd.DataFrame([{'Count': count}])

        elif parsed_query.get('intent') == 'aggregate':
            # Aggregate without grouping
            agg_results = {}

            for agg_spec in parsed_query.get('aggregations', []):
                col = agg_spec['column']
                func = agg_spec['function']

                if col in result_df.columns:
                    if func == 'sum':
                        agg_results[f'Total_{col}'] = [result_df[col].sum()]
                    elif func == 'avg':
                        agg_results[f'Average_{col}'] = [result_df[col].mean()]

            if agg_results:
                result_df = pd.DataFrame(agg_results)
            else:
                result_df = pd.DataFrame([{'Count': len(result_df)}])

        # Apply ordering
        if parsed_query.get('order_by'):
            order_col = parsed_query['order_by']['column']
            direction = parsed_query['order_by']['direction'] == 'asc'

            if order_col in result_df.columns:
                result_df = result_df.sort_values(order_col, ascending=direction)

        # Apply limit
        if parsed_query.get('limit'):
            result_df = result_df.head(parsed_query['limit'])

        return result_df

    def query(self, db: Session, natural_query: str, doc_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Main entry point: natural language query → results

        Example: "How many sales did Bob make?"
        """

        # 1. Get data
        df = self.get_document_data(db, doc_id)

        # 2. Analyze schema
        schema = self.analyze_schema(df)

        # 3. Parse natural language query
        parsed_query = self.query_engine.parse_query(natural_query, schema)

        # 4. Execute query
        result_df = self.execute_query(df, parsed_query)

        # 5. Format response
        return {
            "query": natural_query,
            "parsed_query": parsed_query,
            "schema": schema,
            "results": result_df.to_dict('records'),
            "row_count": len(result_df),
            "chart_suggestion": self._suggest_chart(result_df, parsed_query)
        }

    # def get_dashboard_data(self, db: Session, doc_id: Optional[str] = None) -> Dict[str, Any]:
    #     """
    #     Get complete dashboard data for a document
    #     Returns KPIs, charts, trends, etc.
    #     """
    #
    #     try:
    #         # Get data
    #         df = self.get_document_data(db, doc_id)
    #         schema = self.analyze_schema(df)
    #
    #         # Build dashboard response
    #         dashboard = {
    #             "kpis": {
    #                 "total_records": len(df),
    #                 "metrics": []
    #             },
    #             "schema": schema,
    #             "charts": [],
    #             "insights": []
    #         }
    #
    #         # Add metrics for numeric columns
    #         for col in schema['numeric_columns'][:5]:  # Top 5 numeric columns
    #             col_lower = col.lower()
    #
    #             metric = {
    #                 "name": col,
    #                 "total": float(df[col].sum()),
    #                 "average": float(df[col].mean()),
    #                 "min": float(df[col].min()),
    #                 "max": float(df[col].max())
    #             }
    #
    #             # Add semantic meaning
    #             if any(word in col_lower for word in ['price', 'revenue', 'amount', 'total']):
    #                 metric["type"] = "currency"
    #                 metric["formatted_total"] = f"${metric['total']:,.2f}"
    #             elif any(word in col_lower for word in ['quantity', 'count', 'qty']):
    #                 metric["type"] = "quantity"
    #                 metric["formatted_total"] = f"{int(metric['total']):,}"
    #             else:
    #                 metric["type"] = "numeric"
    #
    #             dashboard["kpis"]["metrics"].append(metric)
    #
    #         # Add simple charts
    #         for cat_col in schema['categorical_columns'][:3]:
    #             if schema['numeric_columns']:
    #                 value_col = schema['numeric_columns'][0]
    #
    #                 # Group by category
    #                 grouped = df.groupby(cat_col)[value_col].sum().reset_index()
    #
    #                 dashboard["charts"].append({
    #                     "id": f"chart_{cat_col}",
    #                     "title": f"{value_col} by {cat_col}",
    #                     "type": "bar",
    #                     "data": {
    #                         "labels": grouped[cat_col].tolist(),
    #                         "values": grouped[value_col].tolist()
    #                     }
    #                 })
    #
    #         return dashboard
    #
    #     except Exception as e:
    #         logger.error(f"Dashboard error: {e}")
    #         import traceback
    #         traceback.print_exc()
    #         raise

    def get_dashboard_data(self, db: Session, doc_id: str) -> Dict[str, Any]:
        """
        Generate complete dashboard data for a document
        """
        logger.info(f"📊 Generating dashboard for document: {doc_id}")

        doc = db.query(Document).filter(Document.id == doc_id).first()

        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        # ✅ Check document type and route to appropriate dashboard
        doc_type = doc.doc_type
        metadata = doc.doc_metadata or {}

        # Bank statement dashboard
        if doc_type == "bank_statement" and "bank_statement_analytics" in metadata:
            return self._generate_bank_statement_dashboard(doc, metadata["bank_statement_analytics"])

        # Invoice dashboard
        if doc_type == "invoice" and "invoice_analytics" in metadata:
            return self._generate_invoice_dashboard(doc, metadata["invoice_analytics"])

        # Receipt dashboard
        if doc_type == "receipt" and "receipt_analytics" in metadata:
            return self._generate_receipt_dashboard(doc, metadata["receipt_analytics"])

        # Default: Try to get tabular data
        try:
            df = self.get_document_data(db, doc_id)

            dashboard = {
                "document_id": doc_id,
                "document_type": doc_type,
                "filename": doc.filename,
                "overview": self._generate_overview(doc, df),
                "visualizations": self._generate_visualizations(df),
                "insights": self._generate_insights(doc, df),
                "metrics": self._calculate_metrics(df)
            }

            return dashboard

        except Exception as e:
            logger.error(f"❌ Error generating dashboard: {e}")
            raise

    def _generate_bank_statement_dashboard(self, doc: Document, analytics: Dict) -> Dict[str, Any]:
        """Generate dashboard specifically for bank statements"""
        logger.info(f"💰 Generating bank statement dashboard")

        summary = analytics.get("summary", {})
        spending_by_category = analytics.get("spending_by_category", {})
        income_analysis = analytics.get("income_analysis", {})
        top_merchants = analytics.get("top_merchants", [])
        financial_health = analytics.get("financial_health", {})

        dashboard = {
            "document_id": doc.id,
            "document_type": "bank_statement",
            "filename": doc.filename,

            "overview": {
                "title": "Bank Statement Analysis",
                "total_transactions": summary.get("total_transactions", 0),
                "total_income": summary.get("total_income", 0),
                "total_expenses": summary.get("total_expenses", 0),
                "net_cash_flow": summary.get("net_cash_flow", 0),
                "financial_health_score": financial_health.get("health_score", 0),
                "financial_health_status": financial_health.get("status", "Unknown")
            },

            "visualizations": [
                {
                    "id": "spending_by_category",
                    "type": "pie",
                    "title": "Spending by Category",
                    "data": {
                        "labels": list(spending_by_category.keys()),
                        "values": list(spending_by_category.values())
                    }
                },
                {
                    "id": "income_vs_expenses",
                    "type": "bar",
                    "title": "Income vs Expenses",
                    "data": {
                        "labels": ["Income", "Expenses"],
                        "values": [
                            summary.get("total_income", 0),
                            summary.get("total_expenses", 0)
                        ]
                    }
                },
                {
                    "id": "top_merchants",
                    "type": "horizontal_bar",
                    "title": "Top 10 Merchants",
                    "data": {
                        "labels": [m["name"] for m in top_merchants[:10]],
                        "values": [m["total_spent"] for m in top_merchants[:10]]
                    }
                }
            ],

            "insights": [
                f"Total transactions: {summary.get('total_transactions', 0)}",
                f"Net cash flow: ${summary.get('net_cash_flow', 0):,.2f}",
                f"Savings rate: {financial_health.get('savings_rate', 0):.1f}%",
                f"Top spending category: {max(spending_by_category, key=spending_by_category.get) if spending_by_category else 'N/A'}"
            ],

            "metrics": {
                "transactions": summary.get("total_transactions", 0),
                "income": summary.get("total_income", 0),
                "expenses": summary.get("total_expenses", 0),
                "net_flow": summary.get("net_cash_flow", 0),
                "avg_transaction": summary.get("average_transaction", 0),
                "health_score": financial_health.get("health_score", 0)
            }
        }

        return dashboard

    def _generate_invoice_dashboard(self, doc: Document, analytics: Dict) -> Dict[str, Any]:
        """Generate dashboard for invoices"""
        logger.info(f"🧾 Generating invoice dashboard")

        invoice_details = analytics.get("invoice_details", {})
        totals = analytics.get("totals", {})
        line_items = analytics.get("line_items", [])

        dashboard = {
            "document_id": doc.id,
            "document_type": "invoice",
            "filename": doc.filename,

            "overview": {
                "title": "Invoice Analysis",
                "invoice_number": invoice_details.get("invoice_number", "N/A"),
                "invoice_date": invoice_details.get("invoice_date", "N/A"),
                "due_date": invoice_details.get("due_date", "N/A"),
                "total_amount": totals.get("total", 0),
                "line_items_count": len(line_items)
            },

            "metrics": {
                "subtotal": totals.get("subtotal", 0),
                "tax": totals.get("tax", 0),
                "total": totals.get("total", 0),
                "line_items": len(line_items)
            },

            "insights": [
                f"Invoice #{invoice_details.get('invoice_number', 'N/A')}",
                f"Total: ${totals.get('total', 0):,.2f}",
                f"Line items: {len(line_items)}"
            ]
        }

        return dashboard

    def _generate_receipt_dashboard(self, doc: Document, analytics: Dict) -> Dict[str, Any]:
        """Generate dashboard for receipts"""
        logger.info(f"🧾 Generating receipt dashboard")

        merchant_info = analytics.get("merchant_info", {})
        totals = analytics.get("totals", {})
        items = analytics.get("items", [])

        dashboard = {
            "document_id": doc.id,
            "document_type": "receipt",
            "filename": doc.filename,

            "overview": {
                "title": "Receipt Analysis",
                "merchant": merchant_info.get("name", "Unknown"),
                "total_amount": totals.get("total", 0),
                "items_count": len(items)
            },

            "metrics": {
                "subtotal": totals.get("subtotal", 0),
                "tax": totals.get("tax", 0),
                "total": totals.get("total", 0),
                "items": len(items)
            },

            "insights": [
                f"Merchant: {merchant_info.get('name', 'Unknown')}",
                f"Total: ${totals.get('total', 0):,.2f}",
                f"Items: {len(items)}"
            ]
        }

        return dashboard

    def _suggest_chart(self, df: pd.DataFrame, query: Dict) -> Dict:
        """Suggest appropriate chart type based on results"""

        if len(df) == 0:
            return {"type": "none"}

        if query.get('group_by'):
            # Has grouping → Bar or Pie chart
            if len(df) <= 10:
                return {
                    "type": "bar",
                    "x": query['group_by'][0],
                    "y": df.columns[-1]  # Last column is usually the aggregation
                }
            else:
                return {"type": "table"}

        return {"type": "table"}