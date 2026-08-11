# app/services/analyzers/qa/test_report_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class TestReportAnalyzer(BaseAnalyzer):
    """
    Analyzer for test reports:
    - Test suite metadata, pass/fail counts, defect summary
    - Pass rate, failure trends, flaky tests
    - Risk flags: high failure rate, critical test failures, long-running tests
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🧪 Test report analysis: {file_path}")

        report = self._extract_report(file_path, text, metadata)
        tests = report.get("tests", [])

        normalized = self._normalize_tests(tests)
        metrics = self._compute_metrics(normalized)
        trends = self._compute_trends(normalized)
        risks = self._risk_checks(metrics, normalized)
        insights = self._generate_insights(report, metrics, trends, risks)
        recommendations = self._generate_recommendations(report, metrics, trends, risks)

        result = {
            "type": "test_report",
            "summary": self._generate_summary(report, metrics),
            "confidence": 0.94,

            "report_id": report.get("report_id"),
            "suite_name": report.get("suite_name"),
            "run_date": report.get("run_date"),
            "environment": report.get("environment"),

            "tests": normalized,
            **metrics,
            "trends": trends,

            "qa_risks": risks,
            "qa_risk_count": len(risks),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_test_report_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # extraction
    def _extract_report(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_test_report_parser import UniversalTestReportParser
            parser = UniversalTestReportParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Test report parsing failed: {e}")
            parsed = {}

        return {
            "report_id": parsed.get("report_id") or metadata.get("report_id"),
            "suite_name": parsed.get("suite_name"),
            "run_date": parsed.get("run_date"),
            "environment": parsed.get("environment"),
            "tests": parsed.get("tests", []),  # [{id, name, status, duration_seconds, severity, flaky}]
        }

    # normalization
    def _normalize_tests(self, tests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for t in tests or []:
            status = str(t.get("status", "unknown")).lower()
            duration = self.safe_float(t.get("duration_seconds", 0))
            severity = t.get("severity", "normal")
            flaky = bool(t.get("flaky", False))
            out.append({
                "id": t.get("id"),
                "name": t.get("name"),
                "status": status,
                "duration_seconds": round(duration, 2),
                "severity": severity,
                "flaky": flaky,
                "_raw": t,
            })
        return out

    # metrics
    def _compute_metrics(self, tests: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(tests)
        passed = sum(1 for t in tests if t["status"] == "passed")
        failed = sum(1 for t in tests if t["status"] == "failed")
        skipped = sum(1 for t in tests if t["status"] == "skipped")
        durations = [t["duration_seconds"] for t in tests if t.get("duration_seconds") is not None]
        avg_duration = round(sum(durations) / len(durations), 2) if durations else 0.0
        flaky_count = sum(1 for t in tests if t.get("flaky"))
        pass_rate = round((passed / total) * 100, 2) if total else 0.0

        severity_counts = {}
        for t in tests:
            s = t.get("severity", "normal")
            severity_counts[s] = severity_counts.get(s, 0) + 1

        return {
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate_percent": pass_rate,
            "average_test_duration_seconds": avg_duration,
            "flaky_test_count": flaky_count,
            "severity_breakdown": severity_counts,
        }

    # trends
    def _compute_trends(self, tests: List[Dict[str, Any]]) -> Dict[str, Any]:
        # simple trend: top slow tests and top failing tests
        slow = sorted([t for t in tests if t.get("duration_seconds") is not None], key=lambda x: x["duration_seconds"], reverse=True)[:10]
        failing = [t for t in tests if t["status"] == "failed"][:10]
        return {
            "top_slow_tests": slow,
            "top_failing_tests": failing,
        }

    # risk checks
    def _risk_checks(self, metrics: Dict[str, Any], tests: List[Dict[str, Any]]) -> List[str]:
        risks = []
        if metrics.get("pass_rate_percent", 100) < 85:
            risks.append("Low pass rate; stability risk for this build.")
        if metrics.get("flaky_test_count", 0) > max(5, metrics.get("total_tests", 0) * 0.05):
            risks.append("High number of flaky tests; test reliability risk.")
        critical_failures = [t for t in tests if t.get("status") == "failed" and t.get("severity") in ("critical", "blocker")]
        if critical_failures:
            risks.append(f"{len(critical_failures)} critical test failures detected.")
        slow_threshold = 300  # seconds
        slow_tests = [t for t in tests if t.get("duration_seconds", 0) > slow_threshold]
        if slow_tests:
            risks.append(f"{len(slow_tests)} tests exceed {slow_threshold}s; performance risk.")
        return risks

    # summary, insights, recommendations
    def _generate_summary(self, report: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        suite = report.get("suite_name", "Test Suite")
        total = metrics.get("total_tests", 0)
        pass_rate = metrics.get("pass_rate_percent", 0.0)
        return f"{suite} run with {total} tests and pass rate {pass_rate:.2f}%."

    def _generate_insights(self, report, metrics, trends, risks) -> List[str]:
        insights = [
            f"Pass rate: {metrics.get('pass_rate_percent', 0.0):.2f}%",
            f"Average test duration: {metrics.get('average_test_duration_seconds', 0.0):.2f}s",
            f"Flaky tests: {metrics.get('flaky_test_count', 0)}",
        ]
        if trends.get("top_failing_tests"):
            insights.append(f"Top failing tests: {len(trends['top_failing_tests'])}")
        if risks:
            insights.append(f"Risks identified: {len(risks)}")
        return insights

    def _generate_recommendations(self, report, metrics, trends, risks) -> List[Dict[str, Any]]:
        recs = []
        if metrics.get("pass_rate_percent", 100) < 85:
            recs.append({"priority": "high", "category": "stability", "title": "Investigate Low Pass Rate", "description": "Triage failing tests and identify root causes.", "potential_savings": 0})
        if metrics.get("flaky_test_count", 0) > 0:
            recs.append({"priority": "medium", "category": "quality", "title": "Stabilize Flaky Tests", "description": "Mark flaky tests and create remediation plan to reduce nondeterminism.", "potential_savings": 0})
        if trends.get("top_slow_tests"):
            recs.append({"priority": "medium", "category": "performance", "title": "Optimize Slow Tests", "description": "Review top slow tests for optimization or isolation.", "potential_savings": 0})
        for r in risks:
            recs.append({"priority": "medium", "category": "risk", "title": "QA Risk", "description": r, "potential_savings": 0})
        return recs

    # UI config
    def build_test_report_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "total_tests", "label": "Total Tests", "value_path": "total_tests", "is_hero": True},
            {"key": "pass_rate_percent", "label": "Pass Rate %", "value_path": "pass_rate_percent", "is_hero": True},
            {"key": "failed", "label": "Failed", "value_path": "failed", "is_hero": True},
            {"key": "flaky_test_count", "label": "Flaky Tests", "value_path": "flaky_test_count", "is_hero": True},
        ]

        charts = [
            {"id": "failure_trend", "type": "bar", "title": "Top Failing Tests", "data_path": "trends.top_failing_tests", "label_field": "name", "value_field": "duration_seconds"},
            {"id": "slow_tests", "type": "bar", "title": "Top Slow Tests", "data_path": "trends.top_slow_tests", "label_field": "name", "value_field": "duration_seconds"},
        ]

        tables = [
            {"id": "tests", "name": "tests", "title": "Test Results", "data_path": "tests"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Test Summary", "metrics": ["total_tests", "pass_rate_percent", "failed", "flaky_test_count"], "charts": [], "tables": []},
            {"id": "trends", "title": "Trends", "metrics": [], "charts": ["failure_trend", "slow_tests"], "tables": ["tests"]},
            {"id": "recommendations", "title": "Recommendations", "metrics": [], "charts": [], "tables": ["recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
