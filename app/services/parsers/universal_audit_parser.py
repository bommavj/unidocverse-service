# app/services/parsers/universal_audit_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalAuditParser(BaseParser):
    """
    Heuristic parser for audit reports.
    Returns a dict with keys used by AuditReportAnalyzer:
      - audit_id
      - auditor_name
      - scope
      - period_start
      - period_end
      - findings: list of {id, title, severity, status, description}
    The parser is defensive: it returns empty defaults if extraction fails.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["audit_id"] = self._extract_audit_id()
            parsed["auditor_name"] = self._extract_auditor()
            parsed["scope"] = self._extract_scope()
            start, end = self._extract_period()
            parsed["period_start"] = start
            parsed["period_end"] = end
            parsed["findings"] = self._extract_findings()
            return parsed
        except Exception as e:
            logger.exception("UniversalAuditParser.parse failed")
            return {
                "audit_id": None,
                "auditor_name": None,
                "scope": None,
                "period_start": None,
                "period_end": None,
                "findings": [],
            }

    # ---------- field extractors ----------

    def _extract_audit_id(self) -> Optional[str]:
        # common labels: Audit ID, Audit No., Report No.
        patterns = [
            r"Audit\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Report\s*(?:No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return v
        # fallback: filename-based id
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_auditor(self) -> Optional[str]:
        # look for "Auditor:" or "Prepared by"
        patterns = [
            r"Auditor[:\s]*([A-Z][A-Za-z ,.&'-]{2,100})",
            r"Prepared by[:\s]*([A-Z][A-Za-z ,.&'-]{2,100})",
            r"Conducted by[:\s]*([A-Z][A-Za-z ,.&'-]{2,100})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # try to find organization lines near top of document
        top = "\n".join(self.text.splitlines()[:10])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,80})\n", top)
        if m:
            return m.group(1).strip()
        return None

    def _extract_scope(self) -> Optional[str]:
        # find a "Scope" heading and capture the following paragraph
        blocks = self.find_all_blocks(r"^\s*Scope\b[:\s]*", end_regex=r"^\s*(?:Findings|Observations|Conclusion|Recommendations)\b")
        if blocks:
            return blocks[0]
        # fallback: look for "scope of audit" phrase
        m = re.search(r"scope of (?:this )?audit[:\s]*(.{20,400})", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(1))
        return None

    def _extract_period(self) -> (Optional[str], Optional[str]):
        # look for "Period: start - end" or "For the period ... to ..."
        m = re.search(r"Period[:\s]*([A-Za-z0-9 ,/-]+?)(?:\n|$)", self.text, flags=re.IGNORECASE)
        if m:
            txt = m.group(1)
            # try to split on 'to' or '-'
            parts = re.split(r"\bto\b|-", txt, flags=re.IGNORECASE)
            if len(parts) >= 2:
                start = self.parse_date(parts[0].strip())
                end = self.parse_date(parts[1].strip())
                return start, end
        # alternative pattern
        m2 = re.search(r"for the period\s+([A-Za-z0-9 ,/-]+?)\s+(?:to|-)\s+([A-Za-z0-9 ,/-]+)", self.text, flags=re.IGNORECASE)
        if m2:
            return self.parse_date(m2.group(1).strip()), self.parse_date(m2.group(2).strip())
        # try to find dates labeled "From" and "To"
        from_vals = self.kv_pairs_near(r"\bFrom\b[:\s]*", window_chars=60)
        to_vals = self.kv_pairs_near(r"\bTo\b[:\s]*", window_chars=60)
        if from_vals and to_vals:
            return self.parse_date(from_vals[0].split()[0]), self.parse_date(to_vals[0].split()[0])
        return None, None

    def _extract_findings(self) -> List[Dict[str, Any]]:
        """
        Attempt to extract findings. Common formats:
          - Numbered list: 1. Title - Severity - Status: description
          - Headings: Finding 1: Title\nDescription...
          - Table-like rows with columns (ID, Title, Severity, Status)
        The parser returns a list of dicts: {id, title, severity, status, description}
        """
        findings: List[Dict[str, Any]] = []

        # 1) Try to extract numbered findings blocks (e.g., "1. Finding title\nDescription...")
        numbered = re.findall(r"^\s*(\d{1,3})[.)]\s*([A-Z][^\n]{5,200})(?:\n(?!\d+[.)]).{0,500})?", self.text, flags=re.MULTILINE)
        if numbered:
            # iterate through matches and capture following paragraph as description
            for m in re.finditer(r"^\s*(\d{1,3})[.)]\s*([^\n]+)", self.text, flags=re.MULTILINE):
                fid = m.group(1).strip()
                title = self.normalize_whitespace(m.group(2))
                # capture description until next numbered item or blank line
                start = m.end()
                end_m = re.search(r"^\s*\d{1,3}[.)]\s+", self.text[start:], flags=re.MULTILINE)
                end = start + (end_m.start() if end_m else len(self.text[start:]))
                desc = self.normalize_whitespace(self.text[start:end])
                findings.append({
                    "id": fid,
                    "title": title,
                    "severity": self._infer_severity(title + " " + desc),
                    "status": self._infer_status(desc),
                    "description": desc,
                })
            if findings:
                return findings

        # 2) Try to extract "Finding" headings
        blocks = self.find_all_blocks(r"^\s*Finding\s*\d*[:\s]*", end_regex=r"^\s*(?:Finding\s*\d*[:\s]*|Recommendation|Conclusion|Observation)")
        if blocks:
            for idx, b in enumerate(blocks, start=1):
                # split first sentence as title
                title = b.split(".")[0][:200]
                findings.append({
                    "id": str(idx),
                    "title": self.normalize_whitespace(title),
                    "severity": self._infer_severity(b),
                    "status": self._infer_status(b),
                    "description": b,
                })
            return findings

        # 3) Try to parse table-like rows (ID | Title | Severity | Status)
        table_rows = re.findall(r"^([A-Za-z0-9\-]+)\s*\|\s*([^\|]{5,200})\s*\|\s*([A-Za-z]+)\s*\|\s*([A-Za-z]+)", self.text, flags=re.MULTILINE)
        if table_rows:
            for r in table_rows:
                findings.append({
                    "id": r[0].strip(),
                    "title": self.normalize_whitespace(r[1]),
                    "severity": r[2].strip().lower(),
                    "status": r[3].strip().lower(),
                    "description": "",
                })
            return findings

        # 4) Fallback: look for "Observation" or "Recommendation" sections and split into items
        obs_blocks = self.find_all_blocks(r"^\s*(?:Observation|Observations|Findings)[:\s]*", end_regex=r"^\s*(?:Recommendation|Recommendations|Conclusion)")
        if obs_blocks:
            text_block = obs_blocks[0]
            # split by semicolon or newline bullets
            parts = re.split(r"\n\s*[-•]\s+|\n\s*\d+[.)]\s+|;\s+", text_block)
            for i, p in enumerate(parts):
                p = self.normalize_whitespace(p)
                if not p:
                    continue
                findings.append({
                    "id": str(i + 1),
                    "title": p.split(".")[0][:200],
                    "severity": self._infer_severity(p),
                    "status": self._infer_status(p),
                    "description": p,
                })
            return findings

        # 5) If nothing found, return empty list
        return []

    # ---------- small heuristics ----------

    def _infer_severity(self, text: str) -> str:
        t = (text or "").lower()
        if "critical" in t or "severe" in t or "high risk" in t:
            return "critical"
        if "high" in t or "major" in t:
            return "high"
        if "medium" in t or "moderate" in t:
            return "medium"
        if "low" in t or "minor" in t:
            return "low"
        # default
        return "medium"

    def _infer_status(self, text: str) -> str:
        t = (text or "").lower()
        if "open" in t and "closed" not in t:
            return "open"
        if "closed" in t or "resolved" in t or "remediated" in t:
            return "closed"
        if "in progress" in t or "ongoing" in t:
            return "in_progress"
        return "open"
