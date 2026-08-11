"""
Universal Intelligence Mixin
Provides standard intelligence layers for all document types
Can be inherited by any analyzer
"""
import logging
from typing import List, Dict, Any
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class IntelligenceMixin:
    """
    Standard intelligence capabilities for any document
    """

    # ========================================
    # ANOMALY DETECTION (Universal)
    # ========================================

    def detect_anomalies_numeric(
            self,
            data: List[Dict],
            field: str,
            threshold: float = 2.0
    ) -> List[Dict]:
        """Detect anomalies in any numeric field"""
        values = [item[field] for item in data if field in item and isinstance(item[field], (int, float))]

        if not values:
            return []

        avg = sum(values) / len(values)
        std = self._std_dev(values)

        anomalies = []
        for item in data:
            if field in item and isinstance(item[field], (int, float)):
                value = item[field]
                z_score = (value - avg) / std if std > 0 else 0

                if abs(z_score) > threshold:
                    anomalies.append({
                        **item,
                        'anomaly_score': round(abs(z_score), 2),
                        'expected_range': f"{avg - std:.2f} - {avg + std:.2f}",
                        'severity': 'critical' if abs(z_score) > 3 else 'high' if abs(z_score) > 2.5 else 'medium'
                    })

        return sorted(anomalies, key=lambda x: x['anomaly_score'], reverse=True)[:10]

    # ========================================
    # PATTERN DETECTION (Universal)
    # ========================================

    def detect_patterns(
            self,
            data: List[Dict],
            group_field: str,
            value_field: str
    ) -> List[Dict]:
        """Detect patterns in any grouped data"""
        groups = defaultdict(lambda: {'count': 0, 'total': 0, 'items': []})

        for item in data:
            if group_field in item and value_field in item:
                key = item[group_field]
                groups[key]['count'] += 1
                groups[key]['total'] += item[value_field]
                groups[key]['items'].append(item)

        patterns = []
        for key, stats in groups.items():
            if stats['count'] >= 2:
                patterns.append({
                    'group': key,
                    'frequency': stats['count'],
                    'total': round(stats['total'], 2),
                    'average': round(stats['total'] / stats['count'], 2),
                    'type': 'recurring' if stats['count'] >= 3 else 'repeated'
                })

        return sorted(patterns, key=lambda x: x['frequency'], reverse=True)

    # ========================================
    # TREND ANALYSIS (Universal)
    # ========================================

    def analyze_trends(
            self,
            data: List[Dict],
            date_field: str,
            value_field: str
    ) -> Dict:
        """Analyze trends over time"""
        by_date = defaultdict(lambda: {'total': 0, 'count': 0})

        for item in data:
            if date_field in item and value_field in item:
                date = item[date_field]
                by_date[date]['total'] += item[value_field]
                by_date[date]['count'] += 1

        sorted_dates = sorted(by_date.items())

        if len(sorted_dates) < 2:
            return {'trend': 'insufficient_data'}

        # Calculate trend
        values = [v['total'] for _, v in sorted_dates]
        first_half = sum(values[:len(values) // 2])
        second_half = sum(values[len(values) // 2:])

        trend = 'increasing' if second_half > first_half else 'decreasing' if second_half < first_half else 'stable'
        change_pct = ((second_half - first_half) / first_half * 100) if first_half > 0 else 0

        return {
            'trend': trend,
            'change_percentage': round(change_pct, 2),
            'first_period_total': round(first_half, 2),
            'second_period_total': round(second_half, 2),
            'data_points': len(sorted_dates)
        }

    # ========================================
    # RISK SCORING (Universal)
    # ========================================

    def calculate_risk_score(self, factors: Dict[str, int]) -> Dict:
        """Calculate risk score from multiple factors"""
        total_score = sum(factors.values())
        capped_score = min(total_score, 100)

        if capped_score >= 75:
            level = 'critical'
        elif capped_score >= 50:
            level = 'high'
        elif capped_score >= 25:
            level = 'medium'
        else:
            level = 'low'

        return {
            'score': capped_score,
            'level': level,
            'factors': factors,
            'description': self._get_risk_description(level)
        }

    def _get_risk_description(self, level: str) -> str:
        descriptions = {
            'critical': 'Immediate attention required',
            'high': 'Review and address soon',
            'medium': 'Monitor closely',
            'low': 'Acceptable risk level'
        }
        return descriptions.get(level, 'Unknown')

    # ========================================
    # COMPLIANCE CHECKING (Universal)
    # ========================================

    def check_compliance(
            self,
            data: Dict,
            required_fields: List[str],
            validation_rules: Dict[str, callable] = None
    ) -> Dict:
        """Check document compliance with rules"""
        issues = []
        warnings = []

        # Check required fields
        for field in required_fields:
            if field not in data or not data[field]:
                issues.append(f"Missing required field: {field}")

        # Run validation rules
        if validation_rules:
            for field, validator in validation_rules.items():
                if field in data:
                    try:
                        if not validator(data[field]):
                            warnings.append(f"Validation failed for: {field}")
                    except Exception as e:
                        warnings.append(f"Validation error for {field}: {str(e)}")

        compliant = len(issues) == 0

        return {
            'compliant': compliant,
            'compliance_score': 100 if compliant else max(0, 100 - len(issues) * 20),
            'issues': issues,
            'warnings': warnings,
            'total_checks': len(required_fields) + (len(validation_rules) if validation_rules else 0)
        }

    # ========================================
    # RECOMMENDATIONS (Universal)
    # ========================================

    def generate_recommendations(
            self,
            context: Dict,
            recommendation_rules: List[Dict]
    ) -> List[Dict]:
        """Generate recommendations based on rules"""
        recommendations = []

        for rule in recommendation_rules:
            condition = rule.get('condition')
            if condition and condition(context):
                recommendations.append({
                    'priority': rule.get('priority', 'medium'),
                    'category': rule.get('category', 'general'),
                    'title': rule.get('title', 'Recommendation'),
                    'description': rule.get('description', ''),
                    'action': rule.get('action', ''),
                    'impact': rule.get('impact', 'medium')
                })

        # Sort by priority
        priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        return sorted(recommendations, key=lambda x: priority_order.get(x['priority'], 2))

    # ========================================
    # KEY FINDINGS (Universal)
    # ========================================

    def extract_key_findings(
            self,
            metrics: Dict,
            finding_rules: List[Dict]
    ) -> List[str]:
        """Extract key findings from metrics"""
        findings = []

        for rule in finding_rules:
            condition = rule.get('condition')
            if condition and condition(metrics):
                template = rule.get('template', '')
                finding = template.format(**metrics)
                findings.append(finding)

        return findings[:10]  # Top 10 findings

    # ========================================
    # HELPERS
    # ========================================

    def _std_dev(self, values: List[float]) -> float:
        """Calculate standard deviation"""
        if not values:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5