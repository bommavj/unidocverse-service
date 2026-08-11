"""
Universal Sales Analyzer v2.0
Smart business metrics - ZERO hardcoding
"""
import logging
from typing import Dict, Any, List, Optional
import pandas as pd
import warnings

from app.services.analyzers.financial.base_financial_analyzer import BaseFinancialAnalyzer

logger = logging.getLogger(__name__)


class SalesAnalyzer(BaseFinancialAnalyzer):
    """
    Universal sales data analyzer
    Automatically detects structure and generates actionable insights
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Main analysis entry point"""
        logger.info(f"🔍 Sales Analyzer: {file_path}")

        try:
            # Load data
            df = self._load_data(file_path)
            df = self.clean_df(df)  # <-- UNIVERSAL FIX APPLIED HERE
            if df is None or df.empty:
                return self._error_response("No data loaded")

            logger.info(f"   📊 {len(df)} rows × {len(df.columns)} columns")

            # Detect column types
            col_types = self._detect_column_types(df)
            logger.info(f"   🔍 Detected: {len(col_types['currency'])} currency, "
                        f"{len(col_types['date'])} date, {len(col_types['category'])} category columns")

            # Calculate metrics
            metrics = self._calculate_metrics(df, col_types)

            # Generate breakdowns
            breakdowns = self._generate_breakdowns(df, col_types)

            # Time series
            time_series = self._analyze_time_series(df, col_types)

            # Insights
            insights = self._generate_insights(df, col_types, metrics, breakdowns)

            # Summary
            summary = self._generate_summary(df, col_types, metrics)

            # Build result
            result = {
                'type': 'sales_data',
                'summary': summary,
                'key_metrics': metrics,
                'insights': insights,
                'alerts': self._check_quality(df),
                'confidence': 0.95
            }

            # Add breakdowns
            result.update(breakdowns)

            # Add time series if available
            if time_series:
                result['time_series'] = time_series

            # Generate UI config
            result = self.add_ui_config(result, 'sales_data')

            logger.info(f"✅ Analysis complete: {len(metrics)} metrics, {len(insights)} insights")
            return result

        except Exception as e:
            logger.error(f"❌ Analysis failed: {e}", exc_info=True)
            return self._error_response(str(e))

    # ============================================================
    # DATA LOADING
    # ============================================================

    def _load_data(self, file_path: str) -> Optional[pd.DataFrame]:
        """Load data from file"""
        try:
            if file_path.endswith('.csv'):
                return pd.read_csv(file_path)
            elif file_path.endswith(('.xlsx', '.xls')):
                return pd.read_excel(file_path)
            elif file_path.endswith('.tsv'):
                return pd.read_csv(file_path, sep='\t')
            return None
        except Exception as e:
            logger.error(f"Load failed: {e}")
            return None

    # ============================================================
    # COLUMN TYPE DETECTION
    # ============================================================

    def _detect_column_types(self, df: pd.DataFrame) -> Dict[str, List[str]]:
        """Detect what each column represents"""
        types = {
            'currency': [],
            'quantity': [],
            'date': [],
            'category': [],
            'id': [],
            'text': []
        }

        for col in df.columns:
            col_lower = str(col).lower()
            dtype = df[col].dtype
            n_unique = df[col].nunique()
            unique_ratio = n_unique / len(df) if len(df) > 0 else 0

            # Dates
            if pd.api.types.is_datetime64_any_dtype(dtype):
                types['date'].append(col)
            elif dtype == 'object' and self._looks_like_date(df[col]):
                types['date'].append(col)

            # Numeric columns
            elif pd.api.types.is_numeric_dtype(dtype):
                # Currency (revenue, price, cost, etc)
                if any(kw in col_lower for kw in ['price', 'revenue', 'sales', 'amount', 'total', 'cost', 'value']):
                    types['currency'].append(col)
                # Quantity
                elif any(kw in col_lower for kw in ['quantity', 'qty', 'units', 'count']):
                    types['quantity'].append(col)
                # Low cardinality numeric = category
                elif unique_ratio < 0.05 and n_unique < 20:
                    types['category'].append(col)

            # Text columns
            elif dtype == 'object':
                # High uniqueness = ID or free text
                if unique_ratio > 0.9:
                    if any(kw in col_lower for kw in ['id', 'number', 'code']):
                        types['id'].append(col)
                    else:
                        types['text'].append(col)
                # Low uniqueness = category
                elif n_unique <= 50:
                    types['category'].append(col)

        return types

    def _looks_like_date(self, series: pd.Series) -> bool:
        """Check if a series contains dates"""
        sample = series.dropna().head(10)
        if len(sample) == 0:
            return False

        try:
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore')
                parsed = pd.to_datetime(sample, errors='coerce')
                return parsed.notna().sum() / len(parsed) >= 0.8
        except:
            return False

    # ============================================================
    # METRICS CALCULATION
    # ============================================================

    def _calculate_metrics(self, df: pd.DataFrame, col_types: Dict) -> Dict[str, Any]:
        """Calculate business metrics"""
        metrics = {'record_count': len(df)}

        # Revenue metrics
        revenue_col = self._get_primary_column(col_types['currency'], ['total', 'price'])
        if revenue_col:
            metrics['total_revenue'] = float(df[revenue_col].sum())
            metrics['avg_transaction_value'] = float(df[revenue_col].mean())

            # Daily average if dates available
            if col_types['date']:
                daily_avg = self._calc_daily_avg(df, revenue_col, col_types['date'][0])
                if daily_avg:
                    metrics['daily_avg_revenue'] = daily_avg
                    metrics['monthly_run_rate'] = daily_avg * 30

        # Quantity metrics
        qty_col = self._get_primary_column(col_types['quantity'], ['quantity', 'qty'])
        if qty_col:
            metrics['total_units'] = float(df[qty_col].sum())
            metrics['avg_units_per_transaction'] = float(df[qty_col].mean())

        # Customer metrics
        customer_col = self._find_customer_column(col_types['category'])
        if customer_col and revenue_col:
            n_customers = df[customer_col].nunique()
            customer_revenue = df.groupby(customer_col)[revenue_col].sum()

            metrics['customer_count'] = int(n_customers)
            metrics['avg_revenue_per_customer'] = float(customer_revenue.mean())
            metrics['top_customer_revenue'] = float(customer_revenue.max())

        # Growth metrics
        if col_types['date'] and revenue_col:
            growth = self._calc_growth(df, revenue_col, col_types['date'][0])
            metrics.update(growth)

        return metrics

    def _get_primary_column(self, columns: List[str], keywords: List[str]) -> Optional[str]:
        """Find the most relevant column from a list"""
        if not columns:
            return None

        # Try to match keywords in order
        for kw in keywords:
            for col in columns:
                if kw in str(col).lower():
                    return col

        # Return first column as fallback
        return columns[0]

    def _find_customer_column(self, category_cols: List[str]) -> Optional[str]:
        """Find customer identifier column"""
        for col in category_cols:
            if 'customer' in str(col).lower() or 'client' in str(col).lower():
                return col
        return None

    def _calc_daily_avg(self, df: pd.DataFrame, value_col: str, date_col: str) -> Optional[float]:
        """Calculate daily average"""
        try:
            df_copy = df[[date_col, value_col]].copy()
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore')
                df_copy[date_col] = pd.to_datetime(df_copy[date_col], errors='coerce')

            df_copy = df_copy.dropna()
            if df_copy.empty:
                return None

            days = (df_copy[date_col].max() - df_copy[date_col].min()).days + 1
            total = df_copy[value_col].sum()
            return float(total / days) if days > 0 else None
        except:
            return None

    def _calc_growth(self, df: pd.DataFrame, value_col: str, date_col: str) -> Dict[str, float]:
        """Calculate growth metrics"""
        try:
            df_copy = df[[date_col, value_col]].copy()
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore')
                df_copy[date_col] = pd.to_datetime(df_copy[date_col], errors='coerce')

            df_copy = df_copy.dropna().sort_values(date_col)
            if len(df_copy) < 2:
                return {}

            # Monthly aggregation
            df_copy['month'] = df_copy[date_col].dt.to_period('M')
            monthly = df_copy.groupby('month')[value_col].sum()

            if len(monthly) < 2:
                return {}

            # MoM growth
            last = monthly.iloc[-1]
            prev = monthly.iloc[-2]
            mom_growth = ((last - prev) / prev * 100) if prev != 0 else 0

            return {'mom_growth_pct': float(mom_growth)}
        except:
            return {}

    # ============================================================
    # BREAKDOWNS
    # ============================================================

    def _generate_breakdowns(self, df: pd.DataFrame, col_types: Dict) -> Dict[str, Any]:
        """Generate breakdowns by category"""
        breakdowns = {}

        value_col = self._get_primary_column(col_types['currency'], ['total', 'price'])
        if not value_col:
            return breakdowns

        for cat_col in col_types['category'][:5]:  # Max 5 breakdowns
            try:
                grouped = df.groupby(cat_col)[value_col].agg(['sum', 'count', 'mean']).reset_index()
                grouped.columns = ['category', 'total', 'count', 'average']
                grouped = grouped.sort_values('total', ascending=False)

                breakdown_name = f"{str(cat_col).lower().replace(' ', '_')}_breakdown"
                breakdowns[breakdown_name] = {
                    'available': True,
                    'dimension': str(cat_col),
                    'value_field': str(value_col),
                    'total_categories': len(grouped),
                    'top_5': grouped.head(5).to_dict('records'),
                    'all_items': dict(zip(grouped['category'].astype(str), grouped['total'].astype(float)))
                }
            except Exception as e:
                logger.debug(f"Breakdown failed for {cat_col}: {e}")

        return breakdowns

    # ============================================================
    # TIME SERIES
    # ============================================================

    def _analyze_time_series(self, df: pd.DataFrame, col_types: Dict) -> Optional[Dict]:
        """Analyze time series if dates available"""
        if not col_types['date']:
            return None

        date_col = col_types['date'][0]
        value_col = self._get_primary_column(col_types['currency'], ['total', 'price'])

        if not value_col:
            return None

        try:
            df_copy = df[[date_col, value_col]].copy()
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore')
                df_copy[date_col] = pd.to_datetime(df_copy[date_col], errors='coerce')

            df_copy = df_copy.dropna()
            if df_copy.empty:
                return None

            daily = df_copy.groupby(df_copy[date_col].dt.date)[value_col].sum().reset_index()
            daily.columns = ['date', 'value']
            daily = daily.sort_values('date')

            return {
                'available': True,
                'date_field': str(date_col),
                'value_field': str(value_col),
                'data_points': len(daily),
                'date_range': {
                    'start': str(daily['date'].min()),
                    'end': str(daily['date'].max())
                },
                'all_times': dict(zip(daily['date'].astype(str), daily['value'].astype(float)))
            }
        except Exception as e:
            logger.debug(f"Time series failed: {e}")
            return None

    # ============================================================
    # INSIGHTS
    # ============================================================

    def _generate_insights(self, df: pd.DataFrame, col_types: Dict, metrics: Dict, breakdowns: Dict) -> List[str]:
        """Generate actionable insights"""
        insights = []

        # Revenue insight
        if 'total_revenue' in metrics:
            rev = metrics['total_revenue']
            avg = metrics.get('avg_transaction_value', 0)
            count = metrics.get('record_count', 0)
            insights.append(f"${rev:,.2f} total revenue across {count:,} transactions (${avg:,.2f} avg)")

        # Run rate
        if 'monthly_run_rate' in metrics:
            mrr = metrics['monthly_run_rate']
            insights.append(f"${mrr:,.2f}/month run rate based on recent activity")

        # Growth
        if 'mom_growth_pct' in metrics:
            growth = metrics['mom_growth_pct']
            direction = "up" if growth > 0 else "down"
            insights.append(f"Revenue {direction} {abs(growth):.1f}% month-over-month")

        # Customers
        if 'customer_count' in metrics:
            count = metrics['customer_count']
            avg_rev = metrics.get('avg_revenue_per_customer', 0)
            insights.append(f"{count:,} customers generating ${avg_rev:,.2f} average revenue each")

        # Top performers
        for name, data in list(breakdowns.items())[:2]:
            if data.get('top_5'):
                top3 = data['top_5'][:3]
                dimension = data['dimension']
                names = ", ".join([str(item['category']) for item in top3])

                top3_total = sum(item['total'] for item in top3)
                all_total = sum(data.get('all_items', {}).values())
                pct = (top3_total / all_total * 100) if all_total > 0 else 0

                insights.append(f"Top 3 {dimension} ({names}) drive {pct:.0f}% of revenue")

        return insights

    # ============================================================
    # SUMMARY
    # ============================================================

    def _generate_summary(self, df: pd.DataFrame, col_types: Dict, metrics: Dict) -> str:
        """Generate executive summary"""
        parts = []

        count = metrics.get('record_count', len(df))
        parts.append(f"Analysis of {count:,} transactions")

        if 'total_revenue' in metrics:
            rev = metrics['total_revenue']
            parts.append(f"${rev:,.2f} total revenue")

        if col_types['category']:
            parts.append(f"analyzed across {len(col_types['category'])} dimensions")

        return ". ".join(parts) + "."

    # ============================================================
    # DATA QUALITY
    # ============================================================

    def _check_quality(self, df: pd.DataFrame) -> List[str]:
        """Check data quality"""
        alerts = []

        # Missing data
        missing_pct = (df.isnull().sum().sum() / (len(df) * len(df.columns))) * 100
        if missing_pct > 5:
            alerts.append(f"⚠️ {missing_pct:.1f}% missing data")

        # Duplicates
        dupes = df.duplicated().sum()
        if dupes > 0:
            alerts.append(f"⚠️ {dupes:,} duplicate rows")

        return alerts

    # In sales_analyzer.py - add to add_ui_config method

    def add_ui_config(self, result: Dict[str, Any], doc_type: str) -> Dict[str, Any]:
        """Enhanced UI config with diverse charts"""

        charts = []
        tables = []
        hero_metrics = []
        sections = []

        # Hero Metrics
        if 'key_metrics' in result:
            metrics = result['key_metrics']

            if 'total_revenue' in metrics:
                hero_metrics.append({
                    'key': 'total_revenue',
                    'label': 'Total Revenue',
                    'value_path': 'key_metrics.total_revenue',
                    'format': 'currency',
                    'icon': '💰',
                    'color': 'green',
                    'is_hero': True
                })

            if 'record_count' in metrics:
                hero_metrics.append({
                    'key': 'transactions',
                    'label': 'Transactions',
                    'value_path': 'key_metrics.record_count',
                    'format': 'number',
                    'icon': '📊',
                    'color': 'blue',
                    'is_hero': True
                })

            if 'avg_transaction_value' in metrics:
                hero_metrics.append({
                    'key': 'avg_value',
                    'label': 'Avg Transaction',
                    'value_path': 'key_metrics.avg_transaction_value',
                    'format': 'currency',
                    'icon': '💵',
                    'color': 'purple',
                    'is_hero': True
                })

            if 'mom_growth_pct' in metrics:
                hero_metrics.append({
                    'key': 'growth',
                    'label': 'MoM Growth',
                    'value_path': 'key_metrics.mom_growth_pct',
                    'format': 'percentage',
                    'icon': '📈',
                    'color': 'orange',
                    'is_hero': True
                })

        # Time Series Chart
        if result.get('time_series', {}).get('available'):
            charts.append({
                'id': 'revenue_over_time',
                'type': 'line',
                'title': 'Revenue Over Time',
                'subtitle': 'Daily revenue trend',
                'data_path': 'time_series.all_times',
                'full_width': True,
                'height': '400px',
                'color_scheme': '#667eea'
            })

        # Breakdown Charts (Bar/Pie)
        for key, breakdown in result.items():
            if isinstance(breakdown, dict) and breakdown.get('available') and 'all_items' in breakdown:
                dimension = breakdown.get('dimension', key)

                # Bar chart for top performers
                charts.append({
                    'id': f'{key}_bar',
                    'type': 'bar',
                    'title': f'Top {dimension}',
                    'data_path': f'{key}.all_items',
                    'color_scheme': 'gradient'
                })

                # Pie chart for distribution
                if breakdown.get('total_categories', 0) <= 10:
                    charts.append({
                        'id': f'{key}_pie',
                        'type': 'pie',
                        'title': f'{dimension} Distribution',
                        'data_path': f'{key}.all_items'
                    })

        # Tables
        for key, breakdown in result.items():
            if isinstance(breakdown, dict) and breakdown.get('top_5'):
                tables.append({
                    'id': key,
                    'name': key,
                    'title': f'Top {breakdown.get("dimension", "Items")}',
                    'data_path': f'{key}.top_5',
                    'show_export': True
                })

        # Organize into sections
        if charts:
            sections.append({
                'id': 'overview',
                'title': 'Revenue Overview',
                'charts': [c['id'] for c in charts[:2]],
                'priority': 1
            })

            if len(charts) > 2:
                sections.append({
                    'id': 'breakdowns',
                    'title': 'Performance Breakdowns',
                    'charts': [c['id'] for c in charts[2:]],
                    'tables': [t['name'] for t in tables],
                    'priority': 2
                })

        result['ui_config'] = {
            'document_type': doc_type,
            'dashboard_type': 'advanced',
            'icon': '💰',
            'suite': 'financial',
            'hero_metrics': hero_metrics,
            'charts': charts,
            'tables': tables,
            'sections': sections,
            'insights_config': {
                'show': True,
                'title': 'Key Insights',
                'icon': '💡',
                'data_path': 'insights'
            },
            'alerts_config': {
                'show': len(result.get('alerts', [])) > 0,
                'title': 'Alerts & Warnings',
                'icon': '⚠️',
                'data_path': 'alerts'
            }
        }

        result['has_advanced_analytics'] = True
        return result


    # ============================================================
    # ERROR HANDLING
    # ============================================================

    def _error_response(self, error: str) -> Dict[str, Any]:
        """Generate error response"""
        return {
            'type': 'sales_data',
            'error': error,
            'summary': f'Analysis failed: {error}',
            'key_metrics': {},
            'insights': [],
            'alerts': [],
            'confidence': 0.0,
            'has_advanced_analytics': False
        }