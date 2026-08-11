"""
Universal Mortgage Parser
app/services/parsers/universal_mortgage_parser.py
"""

import re
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class UniversalMortgageParser:

    def __init__(self, file_path: str = "", text: str = ""):
        self.file_path = file_path
        self.text = text

    def parse(self) -> Dict[str, Any]:
        text = self.text
        if not text:
            return {}

        clean = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        clean = clean.replace('&amp;', '&')

        doc_subtype = self._detect_subtype(clean)
        logger.info(f"🏦 Mortgage subtype detected: {doc_subtype}")

        is_le = doc_subtype in ("loan_estimate", "closing_disclosure")

        if is_le:
            # LE/CD: regex is reliable — docling produces consistent markdown tables
            parsed = self._regex_extract_le(clean)
            llm = self._llm_extract(clean, doc_subtype)
            for key, val in llm.items():
                if not parsed.get(key) and val:
                    parsed[key] = val
        else:
            # Statements/origination: regex first, LLM fills gaps
            parsed = self._regex_extract_statement(clean)
            llm = self._llm_extract(clean, doc_subtype)
            for key, val in llm.items():
                if not parsed.get(key) and val:
                    parsed[key] = val

        parsed["doc_subtype"] = doc_subtype

        # Normalize fallbacks
        if not parsed.get("loan_amount") and parsed.get("current_balance"):
            parsed["loan_amount"] = parsed["current_balance"]
        if not parsed.get("monthly_payment") and parsed.get("payment_due"):
            parsed["monthly_payment"] = parsed["payment_due"]
        if not parsed.get("monthly_payment") and parsed.get("estimated_total_monthly"):
            parsed["monthly_payment"] = parsed["estimated_total_monthly"]

        # Kill false balloon payments
        bp = parsed.get("balloon_payment")
        if bp is not None:
            try:
                if float(bp) < 1000 and re.search(r'balloon\s*payment[\s\t\n]+no\b', clean, re.I):
                    parsed["balloon_payment"] = None
            except (ValueError, TypeError):
                parsed["balloon_payment"] = None

        return parsed

    # ─── SUBTYPE DETECTION ────────────────────────────────────────

    def _detect_subtype(self, text: str) -> str:
        t = text.lower()

        # Payoff statement — detect FIRST
        if re.search(r'^payoff\s*statement\b', t, re.MULTILINE) or \
                re.search(r'payoff\s*quote\b', t) or \
                re.search(r'this\s*(?:is\s*your\s*)?payoff', t) or \
                re.search(r'loan payoff information', t):
            return "payoff_statement"

        # Must have statement header pattern (Statement Date: + Account Number:)
        if re.search(r'statement\s*date\s*:.*?account\s*(?:number|#)\s*:', t, re.DOTALL) or \
                re.search(r'(?:loan|mortgage|account)\s*statement\b', t) or \
                re.search(r'payment\s*due\s*date\s*:.*?total\s*amount\s*due\s*:', t, re.DOTALL):
            return "monthly_statement"

        # Closing Disclosure — standalone header
        if re.search(r'^closing\s*disclosure\s*$', t, re.MULTILINE) or \
                re.search(r'closing\s*disclosure\s*[\•\-\|]', t):
            return "closing_disclosure"

        # Loan Estimate — standalone header or CFPB save-phrase
        if re.search(r'^loan\s*estimate\s*$', t, re.MULTILINE) or \
                re.search(r'save\s*this\s*loan\s*estimate', t) or \
                re.search(r'loan\s*estimate\s*[\•\-\|]', t):
            return "loan_estimate"

        if re.search(r'(heloc|home\s*equity\s*line)', t):
            return "heloc"
        if re.search(r'(adjustable|arm|variable)\s*rate\s*(mortgage|notice|rider)', t):
            return "arm"
        if re.search(r'(promissory\s*note|deed\s*of\s*trust|mortgage\s*deed)', t):
            return "origination"
        return "mortgage_document"

    # ─── LE / CD REGEX (markdown table layout from docling) ───────

    def _regex_extract_le(self, text: str) -> Dict[str, Any]:
        return {
            "loan_number": self._find(text, [
                r'loan\s*id\s*#?\s*\n\s*([A-Z0-9\-]{6,20})',
                r'loan\s*id\s*#\s*\n([^\n]+)',
            ]),
            "borrower_name": self._find(text, [
                r'applicants?\s*\n+\s*([A-Z][a-z]+(?: [A-Z][a-z]+)+)',
            ]),
            "lender_name": self._find(text, [
                r'^([A-Z][A-Za-z0-9\s&,\.]+(?:Bank|Trust|Mortgage|Financial|Lending|Home Loans)[A-Za-z0-9\s&,\.]*)',
            ]),
            "property_address": self._find(text, [
                r'property\s*\n+([A-Z][a-z].+?)\n',
            ]),
            "loan_date": self._find(text, [
                r'date\s*issued\s*\n\s*([\d/\-]+)',
            ]),
            "loan_amount": self._find_amount(text, [
                r'\|\s*Loan Amount\s*\|\s*\$?([\d,]+(?:\.\d{2})?)\s*\|',
                r'\$([\d,]{4,})\n[\d\.]+\s*%\n\$[\d,]+\.\d{2}',
            ]),
            "interest_rate": self._find(text, [
                r'([\d]+\.[\d]+)\s*%\s*\n\$[\d,]+\.\d{2}',
                r'interest\s*rate\s*\n\s*([\d]+\.[\d]+)\s*%?',
            ]),
            "term_years": self._find(text, [
                r'([\d]+)\s*years?\s*\n\s*(?:refinance|purchase|construction)',
                r'loan\s*term[\s\t\n]*([\d]+)\s*years?',
            ]),
            "monthly_payment": self._find_amount(text, [
                r'\|\s*Principal\s*&\s*Interest\s*\|[^|]*\|\s*\$?([\d,]+(?:\.\d{2})?)\s*\|',
                r'principal\s*&\s*interest\s*\n\s*\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "estimated_total_monthly": self._find_amount(text, [
                r'\|\s*Estimated Total Monthly Payment\s*\|\s*\$?([\d,]+(?:\.\d{2})?)\s*\|',
                r'estimated\s*total\s*monthly\s*payment\s*\n\s*\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "rate_type": (
                "variable" if re.search(r'\b(ARM|adjustable[\-\s]rate|variable[\-\s]rate)\b', text, re.I)
                else "fixed" if re.search(r'\bFixed\s*Rate\b', text)
                else None
            ),
            "loan_purpose": self._find(text, [
                r'(?:^|\n)(purchase|refinance|construction)\s*\n',
            ]),
            "balloon_payment": (
                None if re.search(r'balloon\s*payment[\s\t\n]+no\b', text, re.I)
                else self._find_amount(text, [r'balloon\s*(?:payment|amount)[:\s]+\$?([\d,]+(?:\.\d{2})?)'])
            ),
            "closing_costs": self._find_amount(text, [
                r'TOTAL CLOSING COSTS[^|]*\|\s*\$?([\d,]+(?:\.\d{2})?)',
                r'estimated\s*closing\s*costs\s*\n\s*\$?([\d,]+(?:\.\d{2})?)',
                r'estimated\s*closing\s*costs[:\s]+\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "cash_to_close": self._find_amount(text, [
                r'\|\s*Estimated Cash to Close\s*\|\s*\$?([\d,]+(?:\.\d{2})?)\s*\|',
                r'estimated\s*cash\s*to\s*close\s*\n\s*\$?([\d,]+(?:\.\d{2})?)',
                r'(?:estimated\s*)?cash\s*to\s*close[:\s]+\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "lender_credits": self._find_amount(text, [
                r'\|\s*Lender Credits\s*\|\s*-\$?([\d,]+(?:\.\d{2})?)\s*\|',
                r'-\s*\$?([\d,]+(?:\.\d{2})?)\s*in\s*lender\s*credits?',
                r'lender\s*credits?\s*-?\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "origination_charges": self._find_amount(text, [
                r'(?:a\.\s*)?origination\s*charges?\s*\n\s*\$?([\d,]+(?:\.\d{2})?)',
                r'(?:a\.\s*)?origination\s*charges?\s*\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "services_no_shop": self._find_amount(text, [
                r'Services\s*You\s*Cannot\s*Shop\s*For\s*\|\s*\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "services_can_shop": self._find_amount(text, [
                r'Services\s*You\s*Can\s*Shop\s*For\s*\|\s*\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "other_costs": self._find_amount(text, [
                r'TOTAL OTHER COSTS[^|]*\|\s*\$?([\d,]+(?:\.\d{2})?)',
            ]),
            "escrow_payment": self._find_amount(text, [
                r'Initial Escrow Payment[^|]*\|[^|]*\|[^|]*\|\s*\$?([\d,]+(?:\.\d{2})?)',
                r'initial\s*escrow\s*payment\s*(?:at\s*closing)?\s*\$?([\d,]+(?:\.\d{2})?)',
            ]),
        }

    # ─── STATEMENT / ORIGINATION REGEX ───────────────────────────

    def _regex_extract_statement(self, text: str) -> Dict[str, Any]:
        sh = self._parse_statement_header(text)

        current_balance = self._find_amount(text, [
            r'[Pp]rincipal\s*[Bb]alance[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            r'[Uu]npaid\s*(?:[Pp]rincipal\s*)?[Bb]alance[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            r'[Oo]utstanding\s*[Bb]alance[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
        ])

        loan_amount = self._find_amount(text, [
            r'[Oo]riginal\s*[Ll]oan\s*[Aa]mount[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            r'[Oo]riginal\s*[Pp]rincipal[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            r'[Aa]mount\s*[Ff]inanced[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
        ]) or current_balance  # fall back to current balance if no original amount

        return {
            "loan_number": self._find(text, [
                r'[Aa]ccount\s*(?:[Nn]umber|#|[Nn]o\.?)[:\s|]+([0-9]{6,20})',
                r'[Ll]oan\s*(?:[Nn]umber|#|[Nn]o\.?)[:\s|]+([A-Z0-9\-]{4,20})',
            ]) or sh.get("loan_number"),

            "borrower_name": self._find(text, [
                # ALL CAPS name on its own line before address digits
                r'(?m)^([A-Z]{2,}(?:\s+[A-Z](?:\s+|\b))?[A-Z]{2,}(?:\s+[A-Z]{2,})*)\s*\n\s*\d+',
                r'[Bb]orrower[:\s|]+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)',
                r'[Mm]ortgagor[:\s|]+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)',
            ]),

            "lender_name": self._find(text, [
                r'(?:[Ll]ender|[Ss]ervicer)[:\s|]+([A-Za-z][A-Za-z0-9\s&,\.]+(?:Bank|Trust|Mortgage|Financial)[A-Za-z0-9\s&,\.]*)',
            ]),

            "property_address": self._find(text, [
                r'[Pp]roperty\s*[Aa]ddress[:\s|]+\|?\s*(\d+[^\|]+?)(?:\s*\||\n)',
                r'[Pp]roperty\s*[Aa]ddress[:\s]+(.+?)(?:\n|$)',
            ]),

            "loan_date": self._find(text, [
                r'(?:[Ll]oan|[Oo]rigination|[Cc]losing|[Nn]ote)\s*[Dd]ate[:\s|]*([\d/\-]+)',
                r'[Dd]ated[:\s]*([\d/\-]{6,10})',
            ]),

            "loan_amount": loan_amount,
            "current_balance": current_balance,

            "interest_rate": self._find(text, [
                r'[Ii]nterest\s*[Rr]ate[:\s|]+([\d\.]+)\s*%',
                r'[Nn]ote\s*[Rr]ate[:\s|]+([\d\.]+)\s*%',
            ]),

            "term_years": self._find(text, [
                r'[Ll]oan\s*[Tt]erm[:\s|]+([\d]+)\s*[Yy]ears?',
                r'([\d]+)[- ][Yy]ear\s*(?:[Ff]ixed|[Ll]oan|[Mm]ortgage)',
                r'[Mm]aturity\s*[Dd]ate[:\s|]+\w+\s+(\d{4})',
            ]),

            "monthly_payment": self._find_amount(text, [
                r'(?:[Pp]rincipal\s*(?:&|and)\s*[Ii]nterest|[Pp]\s*&\s*[Ii])[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
                r'[Mm]onthly\s*[Pp]ayment[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            ]),

            "next_payment_date": self._find(text, [
                r'[Pp]ayment\s*[Dd]ue\s*[Dd]ate[:\s|]*([\d/\-]+)',
                r'[Dd]ue\s*[Dd]ate[:\s|]*([\d/\-]{6,10})',
            ]) or sh.get("payment_due_date"),

            "payment_due": sh.get("payment_due") or self._find_amount(text, [
                r'[Tt]otal\s*[Aa]mount\s*[Dd]ue[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
                r'[Pp]ayment\s*[Dd]ue[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            ]),

            "escrow_balance": self._find_amount(text, [
                r'[Ee]scrow\s*[Bb]alance[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            ]),

            "maturity_date": self._find(text, [
                r'[Mm]aturity\s*[Dd]ate[:\s|]+([A-Za-z]+\s*\d{4})',
            ]),

            "ytd_interest": self._find_amount(text, [
                r'(?:[Yy][Tt][Dd]|[Yy]ear[\-\s][Tt]o[\-\s][Dd]ate)\s*[Ii]nterest[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
                r'[Ii]nterest\s*[Pp]aid\s*(?:[Yy][Tt][Dd]|[Tt]his\s*[Yy]ear)[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            ]),

            "payoff_amount": self._find_amount(text, [
                r'(?:[Tt]otal\s*)?[Pp]ayoff\s*(?:[Aa]mount|[Bb]alance)[:\s|]+\$?([\d,]+(?:\.\d{2})?)',
            ]),

            "payoff_date": self._find(text, [
                r'[Pp]ayoff\s*(?:[Gg]ood\s*[Tt]hrough|[Dd]ate)[:\s|]+([\d/\-]+)',
            ]),

            "per_diem": self._find_amount(text, [
                r'[Pp]er\s*[Dd]iem[:\s|]+\$?([\d,]+(?:\.\d{2,4})?)',
            ]),

            "apr": self._find(text, [
                r'\bAPR\b[:\s|]+([\d\.]+)\s*%',
                r'[Aa]nnual\s*[Pp]ercentage\s*[Rr]ate[:\s|]+([\d\.]+)\s*%',
            ]),

            "rate_type": (
                "variable" if re.search(r'\b(ARM|adjustable[\-\s]rate|variable[\-\s]rate)\b', text, re.I)
                else "fixed" if re.search(r'\bfixed[\-\s]rate\b|\bFixed\s+Rate\b', text)
                else None
            ),

            "balloon_payment": (
                None if re.search(r'balloon\s*payment[\s\t\n|]+(?:no|none)\b', text, re.I)
                else self._find_amount(text, [r'[Bb]alloon\s*(?:[Pp]ayment|[Aa]mount)[:\s|]+\$?([\d,]+(?:\.\d{2})?)'])
            ),
        }

    # ─── LLM EXTRACTION ──────────────────────────────────────────

    def _llm_extract(self, text: str, doc_subtype: str) -> Dict[str, Any]:
        try:
            import ollama
            from app.core.config import settings
            model = getattr(settings, "OLLAMA_MODEL", "phi3:mini")

            extra_fields = ""
            if doc_subtype in ("loan_estimate", "closing_disclosure"):
                extra_fields = "\n- closing_costs, cash_to_close, lender_credits, origination_charges, services_no_shop, services_can_shop, other_costs, escrow_payment, estimated_total_monthly, loan_purpose"

            prompt = f"""Extract from this mortgage document. Return ONLY JSON, no explanation.

Fields (null if not found, numbers only no $ or commas):
- loan_number, borrower_name, lender_name, property_address, loan_date
- loan_amount (mortgage loan amount NOT property value), interest_rate, term_years
- monthly_payment (P&I only), apr, balloon_payment (null if NO), rate_type (fixed/variable)
- current_balance, next_payment_date, payment_due, ytd_interest{extra_fields}

Text:
{text[:2000]}

JSON:"""

            response = ollama.generate(model=model, prompt=prompt)
            raw = response.get("response", "").strip()

            if "```" in raw:
                for part in raw.split("```"):
                    if "{" in part:
                        raw = part.lstrip("json").strip()
                        break

            start = raw.find("{")
            if start < 0:
                raise ValueError("No JSON object")
            # Find the matching closing brace for the first object
            depth = 0
            end = start
            for i, ch in enumerate(raw[start:], start):
                if ch == "{": depth += 1
                elif ch == "}": 
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end <= start:
                raise ValueError("No JSON object")
            raw = raw[start:end]

            raw = re.sub(r",\s*([}\]])", r"\1", raw)
            raw = re.sub(r":\s*'([^']*)'", r': "\1"', raw)
            raw = re.sub(r"'([^']+)'\s*:", r'"\1":', raw)
            raw = re.sub(r":\s*True\b",  ": true",  raw)
            raw = re.sub(r":\s*False\b", ": false", raw)
            raw = re.sub(r":\s*None\b",  ": null",  raw)
            raw = re.sub(r"//[^\n]*", "", raw)
            raw = re.sub(r"#[^\n]*",  "", raw)

            parsed = json.loads(raw)
            logger.info("✅ LLM mortgage extraction succeeded")

            numeric = ["loan_amount", "interest_rate", "term_years", "monthly_payment",
                       "apr", "balloon_payment", "current_balance", "closing_costs",
                       "cash_to_close", "lender_credits", "origination_charges",
                       "services_no_shop", "services_can_shop", "other_costs",
                       "escrow_payment", "estimated_total_monthly"]
            for f in numeric:
                v = parsed.get(f)
                if v is not None and isinstance(v, str):
                    cleaned = v.replace("$", "").replace(",", "").strip()
                    parsed[f] = cleaned if cleaned.lower() not in ("null", "none", "no", "") else None

            return parsed

        except Exception as e:
            logger.warning(f"⚠️ LLM mortgage extraction failed: {e}")
            return {}

    # ─── STATEMENT HEADER ─────────────────────────────────────────

    def _parse_statement_header(self, text: str) -> Dict[str, Any]:
        result = {}
        m = re.search(
            r'Statement Date:.*?Account Number:.*?Payment Due Date:.*?Total Amount Due:',
            text, re.IGNORECASE | re.DOTALL
        )
        if m:
            after = text[m.end():m.end() + 300].strip()
            lines = [l.strip() for l in after.split('\n') if l.strip()]
            if lines:
                parts = lines[0].split()
                if len(parts) >= 2:
                    result["statement_date"] = parts[0]
                    result["loan_number"] = parts[1]
            if len(lines) >= 2:
                result["payment_due_date"] = lines[1]
            if len(lines) >= 3:
                result["payment_due"] = lines[2].replace('$', '').replace(',', '').strip()
        return result

    # ─── HELPERS ──────────────────────────────────────────────────

    def _find(self, text: str, patterns: list) -> Optional[str]:
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE | re.MULTILINE)
            if m:
                return m.group(1).strip()
        return None

    def _find_amount(self, text: str, patterns: list) -> Optional[str]:
        val = self._find(text, patterns)
        return val.replace(",", "") if val else None