from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal

from app.parsers.extractors.base_extractor import BaseExtractor


class LegalAgreementExtractor(BaseExtractor):
    """
    Extract data from Legal Agreements including:
    - Agreement type and metadata
    - Party information (all signatories)
    - Key terms and conditions
    - Financial obligations
    - Dates (effective, termination, renewal)
    - Governing law and jurisdiction
    - Termination clauses
    - Confidentiality provisions
    - Liability and indemnification
    - Signature information
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Determine agreement type
        agreement_type = self._determine_agreement_type(text)

        # Extract basic metadata
        title = self._extract_title(text)
        agreement_number = self._extract_agreement_number(text)

        # Extract dates
        dates = self._extract_dates(text)

        # Extract parties
        parties = self._extract_parties(text)

        # Extract recitals/whereas clauses
        recitals = self._extract_recitals(text)

        # Extract scope and purpose
        scope = self._extract_scope(text)

        # Extract financial terms
        financial_terms = self._extract_financial_terms(text, tables, agreement_type)

        # Extract term and termination
        term_info = self._extract_term_info(text)

        # Extract termination clauses
        termination = self._extract_termination_clauses(text)

        # Extract confidentiality provisions
        confidentiality = self._extract_confidentiality(text)

        # Extract intellectual property clauses
        ip_provisions = self._extract_ip_provisions(text)

        # Extract liability and indemnification
        liability = self._extract_liability_provisions(text)

        # Extract dispute resolution
        dispute_resolution = self._extract_dispute_resolution(text)

        # Extract governing law
        governing_law = self._extract_governing_law(text)

        # Extract representations and warranties
        representations = self._extract_representations(text)

        # Extract compliance and regulatory
        compliance = self._extract_compliance_provisions(text)

        # Extract signature information
        signatures = self._extract_signatures(text)

        # Extract amendments and modifications
        amendments = self._extract_amendment_provisions(text)

        # Extract notice provisions
        notices = self._extract_notice_provisions(text)

        # Validation
        validation = self._validate_agreement(parties, dates, signatures)

        return {
            # Metadata
            'document_type': 'legal_agreement',
            'agreement_type': agreement_type,
            'title': title,
            'agreement_number': agreement_number,
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Dates
            'effective_date': dates.get('effective'),
            'execution_date': dates.get('execution'),
            'commencement_date': dates.get('commencement'),
            'expiration_date': dates.get('expiration'),
            'termination_date': dates.get('termination'),

            # Parties
            'parties': parties,
            'party_count': len(parties),
            'primary_party': parties[0] if parties else None,
            'counterparty': parties[1] if len(parties) > 1 else None,

            # Recitals and Background
            'recitals': recitals,
            'whereas_clauses': recitals,  # Alias

            # Scope
            'purpose': scope.get('purpose'),
            'scope_of_work': scope.get('scope_of_work'),
            'deliverables': scope.get('deliverables'),

            # Financial Terms
            'financial_terms': financial_terms,
            'contract_value': financial_terms.get('total_value'),
            'payment_terms': financial_terms.get('payment_terms'),
            'pricing': financial_terms.get('pricing'),

            # Term and Duration
            'term': term_info.get('initial_term'),
            'initial_term': term_info.get('initial_term'),
            'renewal_terms': term_info.get('renewal_terms'),
            'auto_renewal': term_info.get('auto_renewal'),
            'notice_period': term_info.get('notice_period'),

            # Termination
            'termination_clauses': termination,
            'termination_for_convenience': termination.get('for_convenience'),
            'termination_for_cause': termination.get('for_cause'),

            # Confidentiality
            'confidentiality': confidentiality,
            'confidentiality_period': confidentiality.get('duration'),

            # Intellectual Property
            'ip_provisions': ip_provisions,
            'ip_ownership': ip_provisions.get('ownership'),
            'license_grants': ip_provisions.get('licenses'),

            # Liability
            'liability': liability,
            'limitation_of_liability': liability.get('limitation'),
            'indemnification': liability.get('indemnification'),
            'insurance_requirements': liability.get('insurance'),

            # Dispute Resolution
            'dispute_resolution': dispute_resolution,
            'arbitration_clause': dispute_resolution.get('arbitration'),
            'mediation_clause': dispute_resolution.get('mediation'),
            'jurisdiction': dispute_resolution.get('jurisdiction'),

            # Governing Law
            'governing_law': governing_law.get('law'),
            'governing_jurisdiction': governing_law.get('jurisdiction'),
            'venue': governing_law.get('venue'),

            # Representations and Warranties
            'representations': representations,
            'warranties': representations.get('warranties'),
            'warranty_period': representations.get('warranty_period'),

            # Compliance
            'compliance': compliance,
            'regulatory_compliance': compliance.get('regulations'),

            # Signatures
            'signatures': signatures,
            'fully_executed': len(signatures) >= len(parties) if parties and signatures else False,
            'execution_status': 'Fully Executed' if len(signatures) >= len(
                parties) else 'Partially Executed' if signatures else 'Not Executed',

            # Amendments
            'amendment_provisions': amendments,
            'amendment_count': amendments.get('count', 0),

            # Notices
            'notice_provisions': notices,

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _determine_agreement_type(self, text: str) -> str:
        """Determine the type of legal agreement"""
        text_lower = text.lower()

        # Service agreements
        if any(keyword in text_lower for keyword in ['service agreement', 'services agreement', 'master service']):
            return 'service_agreement'
        elif 'consulting agreement' in text_lower:
            return 'consulting_agreement'

        # Employment and contractor
        elif any(keyword in text_lower for keyword in ['employment agreement', 'employment contract']):
            return 'employment_agreement'
        elif any(keyword in text_lower for keyword in ['independent contractor', 'contractor agreement']):
            return 'contractor_agreement'

        # Sales and purchase
        elif any(keyword in text_lower for keyword in ['purchase agreement', 'asset purchase']):
            return 'purchase_agreement'
        elif 'sales agreement' in text_lower:
            return 'sales_agreement'

        # Licensing
        elif any(keyword in text_lower for keyword in ['license agreement', 'licensing agreement', 'software license']):
            return 'license_agreement'
        elif 'sublicense' in text_lower:
            return 'sublicense_agreement'

        # Real estate
        elif any(keyword in text_lower for keyword in ['lease agreement', 'rental agreement', 'tenancy agreement']):
            return 'lease_agreement'

        # Partnership and collaboration
        elif any(keyword in text_lower for keyword in ['partnership agreement', 'joint venture']):
            return 'partnership_agreement'
        elif 'collaboration agreement' in text_lower:
            return 'collaboration_agreement'

        # Confidentiality
        elif any(keyword in text_lower for keyword in ['non-disclosure', 'nda', 'confidentiality agreement']):
            return 'nda'

        # Non-compete
        elif any(keyword in text_lower for keyword in ['non-compete', 'non-competition']):
            return 'non_compete'

        # Distribution and reseller
        elif any(keyword in text_lower for keyword in ['distribution agreement', 'distributor agreement']):
            return 'distribution_agreement'
        elif 'reseller agreement' in text_lower:
            return 'reseller_agreement'

        # Settlement
        elif 'settlement agreement' in text_lower:
            return 'settlement_agreement'

        # Merger and acquisition
        elif any(keyword in text_lower for keyword in ['merger agreement', 'acquisition agreement']):
            return 'merger_agreement'

        # SaaS and subscription
        elif any(keyword in text_lower for keyword in ['saas agreement', 'subscription agreement']):
            return 'saas_agreement'

        return 'general_agreement'

    def _extract_title(self, text: str) -> Optional[str]:
        """Extract agreement title"""
        # Usually in the first few lines, often all caps or centered
        lines = text.split('\n')[:10]

        for line in lines:
            line = line.strip()
            # Look for lines with "AGREEMENT" in caps
            if 'AGREEMENT' in line and len(line) < 150:
                return line
            # Or lines that look like titles
            if line.isupper() and 20 < len(line) < 150 and 'AGREEMENT' in line:
                return line

        return None

    def _extract_agreement_number(self, text: str) -> Optional[str]:
        """Extract agreement reference number"""
        patterns = [
            r'Agreement\s+(?:Number|No\.|#):?\s*([A-Z0-9-]+)',
            r'Contract\s+(?:Number|No\.|#):?\s*([A-Z0-9-]+)',
            r'Reference\s+(?:Number)?:?\s*([A-Z0-9-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_dates(self, text: str) -> Dict[str, Optional[str]]:
        """Extract various agreement dates"""
        dates = {}

        # Effective date
        effective_patterns = [
            r'Effective\s+Date:?\s*(\w+\s+\d{1,2},?\s+\d{4})',
            r'Effective\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'effective as of\s+(\w+\s+\d{1,2},?\s+\d{4})',
            r'entered into\s+(?:as of\s+)?(\w+\s+\d{1,2},?\s+\d{4})',
        ]

        for pattern in effective_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dates['effective'] = match.group(1).strip()
                break

        # Execution date
        execution_patterns = [
            r'(?:executed|signed)\s+(?:on|this)\s+(\d{1,2}(?:st|nd|rd|th)?\s+day of\s+\w+,?\s+\d{4})',
            r'Date of Execution:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        ]

        for pattern in execution_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dates['execution'] = match.group(1).strip()
                break

        # Commencement date
        commencement_patterns = [
            r'Commencement\s+Date:?\s*(\w+\s+\d{1,2},?\s+\d{4})',
            r'Start\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        ]

        for pattern in commencement_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dates['commencement'] = match.group(1).strip()
                break

        # Expiration date
        expiration_patterns = [
            r'Expiration\s+Date:?\s*(\w+\s+\d{1,2},?\s+\d{4})',
            r'End\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'expires?\s+on\s+(\w+\s+\d{1,2},?\s+\d{4})',
        ]

        for pattern in expiration_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dates['expiration'] = match.group(1).strip()
                break

        # Termination date
        termination_pattern = r'Termination\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        match = re.search(termination_pattern, text, re.IGNORECASE)
        if match:
            dates['termination'] = match.group(1).strip()

        return dates

    def _extract_parties(self, text: str) -> List[Dict[str, Any]]:
        """Extract all parties to the agreement"""
        parties = []

        # Common pattern: "This Agreement is entered into between [Party A] and [Party B]"
        between_pattern = r'between\s+(.+?)\s+(?:and|&)\s+(.+?)(?:,|\.|;|\()'
        match = re.search(between_pattern, text, re.IGNORECASE | re.DOTALL)

        if match:
            party1_text = match.group(1).strip()
            party2_text = match.group(2).strip()

            parties.append(self._parse_party(party1_text))
            parties.append(self._parse_party(party2_text))
        else:
            # Alternative: Look for signature blocks
            parties = self._extract_parties_from_signatures(text)

        # If still no parties, look for common party indicators
        if not parties:
            parties = self._extract_parties_generic(text)

        return parties

    def _parse_party(self, party_text: str) -> Dict[str, Any]:
        """Parse party information from text"""
        party = {
            'name': None,
            'type': None,  # 'individual' or 'entity'
            'role': None,
            'address': None,
            'registration': None,
        }

        # Clean up the text
        party_text = party_text.strip()

        # Extract role (in parentheses or after comma)
        role_match = re.search(r'\(([^)]+)\)', party_text)
        if role_match:
            party['role'] = role_match.group(1).strip()
            party_text = party_text.replace(role_match.group(0), '').strip()

        # Check if entity (has Inc, LLC, Corp, etc.)
        entity_indicators = ['Inc.', 'LLC', 'Corp', 'Ltd', 'Limited', 'Corporation', 'Company', 'LP', 'LLP']
        if any(indicator in party_text for indicator in entity_indicators):
            party['type'] = 'entity'
        else:
            party['type'] = 'individual'

        # Extract name (what remains)
        party['name'] = party_text.strip(' ,;')

        return party

    def _extract_parties_from_signatures(self, text: str) -> List[Dict[str, Any]]:
        """Extract parties from signature blocks"""
        parties = []

        # Look for signature block pattern
        sig_pattern = r'(?:BY|For|On behalf of):\s*([A-Z][A-Za-z\s&.,Inc]+?)(?:\n|By:)'
        matches = re.finditer(sig_pattern, text, re.IGNORECASE)

        for match in matches:
            party = self._parse_party(match.group(1))
            if party['name'] and party not in parties:
                parties.append(party)

        return parties

    def _extract_parties_generic(self, text: str) -> List[Dict[str, Any]]:
        """Generic party extraction as fallback"""
        parties = []

        # Look for "Party A", "Party B" style
        lines = text.split('\n')[:30]  # Check first 30 lines

        for line in lines:
            # Entity indicators
            if any(ind in line for ind in ['Inc.', 'LLC', 'Corp', 'Ltd']):
                party = {
                    'name': line.strip(),
                    'type': 'entity',
                    'role': None,
                }
                if party not in parties:
                    parties.append(party)

        return parties[:10]  # Limit to reasonable number

    def _extract_recitals(self, text: str) -> List[str]:
        """Extract WHEREAS clauses / recitals"""
        recitals = []

        # Pattern: "WHEREAS, [text]"
        whereas_pattern = r'WHEREAS,?\s+(.+?)(?=WHEREAS|NOW THEREFORE|Article|Section|\n\n)'
        matches = re.finditer(whereas_pattern, text, re.IGNORECASE | re.DOTALL)

        for match in matches:
            recital = match.group(1).strip()
            if recital:
                recitals.append(recital)

        return recitals

    def _extract_scope(self, text: str) -> Dict[str, Any]:
        """Extract scope, purpose, and deliverables"""
        scope = {}

        # Purpose
        purpose_patterns = [
            r'Purpose\.?\s+(.+?)(?=\n\n|Article|Section)',
            r'WHEREAS.*purpose\s+(?:is|of)\s+(.+?)(?=WHEREAS|NOW|;)',
        ]

        for pattern in purpose_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                scope['purpose'] = match.group(1).strip()[:500]
                break

        # Scope of work
        sow_patterns = [
            r'Scope of Work[:\.]?\s+(.+?)(?=\n\n|Article|Section)',
            r'Services[:\.]?\s+(.+?)(?=\n\n|Article|Section)',
        ]

        for pattern in sow_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                scope['scope_of_work'] = match.group(1).strip()[:500]
                break

        # Deliverables
        deliverables_pattern = r'Deliverables[:\.]?\s+(.+?)(?=\n\n|Article|Section)'
        match = re.search(deliverables_pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            scope['deliverables'] = match.group(1).strip()[:500]

        return scope

    def _extract_financial_terms(self, text: str, tables: List, agreement_type: str) -> Dict[str, Any]:
        """Extract financial terms and obligations"""
        financial = {}

        # Contract value
        value_patterns = [
            r'total\s+contract\s+value.*?\$\s*([\d,]+\.?\d*)',
            r'total\s+amount.*?\$\s*([\d,]+\.?\d*)',
            r'purchase\s+price.*?\$\s*([\d,]+\.?\d*)',
        ]

        for pattern in value_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                financial['total_value'] = self._parse_financial_value(match.group(1))
                break

        # Payment terms
        payment_patterns = [
            r'Payment\s+Terms?[:\.]?\s+(.+?)(?=\n\n|Article|Section)',
            r'Compensation[:\.]?\s+(.+?)(?=\n\n|Article|Section)',
        ]

        for pattern in payment_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                financial['payment_terms'] = match.group(1).strip()[:300]
                break

        # Pricing details
        pricing = self._extract_pricing(text, tables)
        if pricing:
            financial['pricing'] = pricing

        # Deposit/upfront payment
        deposit_pattern = r'deposit.*?\$\s*([\d,]+\.?\d*)'
        match = re.search(deposit_pattern, text, re.IGNORECASE)
        if match:
            financial['deposit'] = self._parse_financial_value(match.group(1))

        # Payment schedule
        schedule = self._extract_payment_schedule(text, tables)
        if schedule:
            financial['payment_schedule'] = schedule

        # Late fees
        late_fee_pattern = r'late\s+fee.*?\$?\s*([\d,]+\.?\d*)'
        match = re.search(late_fee_pattern, text, re.IGNORECASE)
        if match:
            financial['late_fee'] = self._parse_financial_value(match.group(1))

        # Currency
        financial['currency'] = self._extract_currency_type(text) or 'USD'

        return financial

    def _extract_pricing(self, text: str, tables: List) -> Optional[Dict[str, Any]]:
        """Extract pricing information"""
        pricing = {}

        # Hourly rate
        hourly_pattern = r'\$\s*([\d,]+\.?\d*)\s+per\s+hour'
        match = re.search(hourly_pattern, text, re.IGNORECASE)
        if match:
            pricing['hourly_rate'] = self._parse_financial_value(match.group(1))

        # Monthly fee
        monthly_pattern = r'\$\s*([\d,]+\.?\d*)\s+per\s+month'
        match = re.search(monthly_pattern, text, re.IGNORECASE)
        if match:
            pricing['monthly_fee'] = self._parse_financial_value(match.group(1))

        # Annual fee
        annual_pattern = r'\$\s*([\d,]+\.?\d*)\s+(?:per\s+year|annually)'
        match = re.search(annual_pattern, text, re.IGNORECASE)
        if match:
            pricing['annual_fee'] = self._parse_financial_value(match.group(1))

        return pricing if pricing else None

    def _extract_payment_schedule(self, text: str, tables: List) -> Optional[List[Dict[str, Any]]]:
        """Extract payment schedule if present"""
        # Check tables for payment schedule
        for table in tables:
            if len(table) < 2:
                continue

            header = [str(cell).lower() for cell in table[0]]
            if 'payment' in ' '.join(header) or 'milestone' in ' '.join(header):
                # This might be a payment schedule
                schedule = []
                for row in table[1:]:
                    if len(row) >= 2:
                        schedule.append({
                            'description': str(row[0]),
                            'amount': self._parse_financial_value(str(row[1])) if len(row) > 1 else None,
                        })
                if schedule:
                    return schedule

        return None

    def _extract_term_info(self, text: str) -> Dict[str, Any]:
        """Extract term and duration information"""
        term = {}

        # Initial term
        term_patterns = [
            r'(?:initial\s+)?term.*?(\d+)\s+(year|month|day)s?',
            r'period of\s+(\d+)\s+(year|month|day)s?',
        ]

        for pattern in term_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                term['initial_term'] = f"{match.group(1)} {match.group(2)}s"
                break

        # Renewal terms
        renewal_pattern = r'renew(?:al)?.*?(\d+)\s+(year|month|day)'
        match = re.search(renewal_pattern, text, re.IGNORECASE)
        if match:
            term['renewal_terms'] = f"{match.group(1)} {match.group(2)}s"

        # Auto-renewal
        if re.search(r'auto(?:matic(?:ally)?)?[\s-]renew', text, re.IGNORECASE):
            term['auto_renewal'] = True
        else:
            term['auto_renewal'] = False

        # Notice period for termination/non-renewal
        notice_patterns = [
            r'(\d+)\s+days?[\s\']?\s+(?:written\s+)?notice',
            r'notice\s+of\s+(?:at\s+least\s+)?(\d+)\s+days?',
        ]

        for pattern in notice_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                term['notice_period'] = f"{match.group(1)} days"
                break

        return term

    def _extract_termination_clauses(self, text: str) -> Dict[str, Any]:
        """Extract termination provisions"""
        termination = {}

        # Termination for convenience
        if re.search(r'terminat(?:e|ion)\s+(?:for|at)\s+(?:any\s+reason|convenience)', text, re.IGNORECASE):
            termination['for_convenience'] = True

            # Extract notice period for convenience termination
            conv_pattern = r'terminat(?:e|ion).*?convenience.*?(\d+)\s+days?'
            match = re.search(conv_pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                termination['convenience_notice'] = f"{match.group(1)} days"
        else:
            termination['for_convenience'] = False

        # Termination for cause
        if re.search(r'terminat(?:e|ion)\s+for\s+cause', text, re.IGNORECASE):
            termination['for_cause'] = True

            # Extract cause reasons
            cause_section = self._extract_section_content(text, 'Termination for Cause')
            if cause_section:
                termination['cause_reasons'] = cause_section[:500]
        else:
            termination['for_cause'] = False

        # Termination for breach
        if re.search(r'terminat(?:e|ion).*?breach', text, re.IGNORECASE):
            termination['for_breach'] = True

        # Termination for insolvency
        if re.search(r'terminat(?:e|ion).*?(?:insolvency|bankruptcy)', text, re.IGNORECASE):
            termination['for_insolvency'] = True

        # Survival provisions
        if re.search(r'surviv(?:e|al)', text, re.IGNORECASE):
            termination['survival_provisions'] = True

        return termination

    def _extract_confidentiality(self, text: str) -> Dict[str, Any]:
        """Extract confidentiality provisions"""
        confidentiality = {}

        # Check if confidentiality section exists
        if re.search(r'confidential(?:ity)?', text, re.IGNORECASE):
            confidentiality['exists'] = True

            # Duration of confidentiality obligation
            duration_patterns = [
                r'confidential.*?(\d+)\s+(year|month)s?',
                r'(?:for\s+a\s+period\s+of|remain\s+confidential\s+for)\s+(\d+)\s+(year|month)s?',
            ]

            for pattern in duration_patterns:
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    confidentiality['duration'] = f"{match.group(1)} {match.group(2)}s"
                    break

            # Check for perpetual confidentiality
            if re.search(r'perpetual|indefinite', text, re.IGNORECASE):
                confidentiality['duration'] = 'Perpetual'

            # Exceptions to confidentiality
            if re.search(r'except(?:ions?)?\s+to\s+confidential', text, re.IGNORECASE):
                confidentiality['has_exceptions'] = True

        else:
            confidentiality['exists'] = False

        return confidentiality

    def _extract_ip_provisions(self, text: str) -> Dict[str, Any]:
        """Extract intellectual property provisions"""
        ip = {}

        # Ownership
        ownership_patterns = [
            r'(?:all\s+)?(?:intellectual\s+property|IP).*?(?:shall\s+)?(?:belong\s+to|owned\s+by|vest\s+in)\s+([A-Za-z\s]+)',
            r'ownership.*?intellectual\s+property.*?([A-Za-z\s]+)',
        ]

        for pattern in ownership_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                ip['ownership'] = match.group(1).strip()[:100]
                break

        # License grants
        if re.search(r'(?:hereby\s+)?grant(?:s|ed)?.*?license', text, re.IGNORECASE):
            ip['license_granted'] = True

            # Type of license
            if re.search(r'non-exclusive', text, re.IGNORECASE):
                ip['license_type'] = 'non-exclusive'
            elif re.search(r'exclusive', text, re.IGNORECASE):
                ip['license_type'] = 'exclusive'

            # License scope
            if re.search(r'worldwide', text, re.IGNORECASE):
                ip['license_scope'] = 'worldwide'
            elif re.search(r'territory', text, re.IGNORECASE):
                ip['license_scope'] = 'territorial'

        # Work for hire
        if re.search(r'work\s+(?:made\s+)?for\s+hire', text, re.IGNORECASE):
            ip['work_for_hire'] = True

        return ip

    def _extract_liability_provisions(self, text: str) -> Dict[str, Any]:
        """Extract liability and indemnification clauses"""
        liability = {}

        # Limitation of liability
        limitation_patterns = [
            r'limit(?:ation)?\s+of\s+liability.*?\$\s*([\d,]+\.?\d*)',
            r'(?:shall\s+not\s+exceed|limited\s+to).*?\$\s*([\d,]+\.?\d*)',
        ]

        for pattern in limitation_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                liability['limitation'] = self._parse_financial_value(match.group(1))
                break

        # Cap on liability
        if re.search(r'cap(?:ped)?\s+(?:at|to)', text, re.IGNORECASE):
            liability['has_cap'] = True

        # Exclusion of consequential damages
        if re.search(r'(?:no|not).*?(?:liable|responsible).*?(?:consequential|indirect|incidental|punitive)\s+damages',
                     text, re.IGNORECASE):
            liability['excludes_consequential'] = True

        # Indemnification
        if re.search(r'indemnif(?:y|ication)', text, re.IGNORECASE):
            liability['indemnification'] = True

            indem_section = self._extract_section_content(text, 'Indemnification')
            if indem_section:
                liability['indemnification_details'] = indem_section[:500]

        # Insurance requirements
        insurance_pattern = r'insurance.*?\$\s*([\d,]+\.?\d*)'
        match = re.search(insurance_pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            liability['insurance'] = self._parse_financial_value(match.group(1))

        return liability

    def _extract_dispute_resolution(self, text: str) -> Dict[str, Any]:
        """Extract dispute resolution mechanisms"""
        dispute = {}

        # Arbitration
        if re.search(r'arbitration', text, re.IGNORECASE):
            dispute['arbitration'] = True

            # Arbitration rules
            if re.search(r'AAA|American Arbitration Association', text, re.IGNORECASE):
                dispute['arbitration_rules'] = 'AAA'
            elif re.search(r'JAMS', text, re.IGNORECASE):
                dispute['arbitration_rules'] = 'JAMS'

            # Binding arbitration
            if re.search(r'binding\s+arbitration', text, re.IGNORECASE):
                dispute['binding_arbitration'] = True

        # Mediation
        if re.search(r'mediation', text, re.IGNORECASE):
            dispute['mediation'] = True

        # Governing law and jurisdiction
        jurisdiction_pattern = r'(?:subject\s+to|governed\s+by).*?(?:laws?\s+of|jurisdiction\s+of)\s+(?:the\s+)?([A-Za-z\s,]+?)(?:\.|,|;)'
        match = re.search(jurisdiction_pattern, text, re.IGNORECASE)
        if match:
            dispute['jurisdiction'] = match.group(1).strip()

        # Venue
        venue_pattern = r'venue.*?(?:shall\s+be|in)\s+([A-Za-z\s,]+?)(?:\.|,|;)'
        match = re.search(venue_pattern, text, re.IGNORECASE)
        if match:
            dispute['venue'] = match.group(1).strip()

        return dispute

    def _extract_governing_law(self, text: str) -> Dict[str, Any]:
        """Extract governing law provisions"""
        governing = {}

        # Governing law
        law_patterns = [
            r'governed\s+by.*?laws?\s+of\s+(?:the\s+)?([A-Za-z\s]+?)(?:\.|,|without)',
            r'subject\s+to.*?laws?\s+of\s+(?:the\s+)?([A-Za-z\s]+?)(?:\.|,)',
        ]

        for pattern in law_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                governing['law'] = match.group(1).strip()
                break

        # Jurisdiction
        jurisdiction_pattern = r'jurisdiction\s+of.*?(?:courts?\s+(?:of|in)|state\s+of)\s+([A-Za-z\s]+)'
        match = re.search(jurisdiction_pattern, text, re.IGNORECASE)
        if match:
            governing['jurisdiction'] = match.group(1).strip()

        # Venue
        venue_pattern = r'(?:exclusive\s+)?venue.*?(?:shall\s+be|located\s+in)\s+([A-Za-z\s,]+?)(?:\.|,)'
        match = re.search(venue_pattern, text, re.IGNORECASE)
        if match:
            governing['venue'] = match.group(1).strip()

        return governing

    def _extract_representations(self, text: str) -> Dict[str, Any]:
        """Extract representations and warranties"""
        reps = {}

        # Check if section exists
        if re.search(r'representations?\s+and\s+warranties', text, re.IGNORECASE):
            reps['exists'] = True

            # Extract the section
            rep_section = self._extract_section_content(text, 'Representations and Warranties')
            if rep_section:
                reps['details'] = rep_section[:1000]

            # Look for specific representations
            if re.search(r'authority\s+to\s+(?:enter|execute)', text, re.IGNORECASE):
                reps['authority'] = True

            if re.search(r'no\s+conflict', text, re.IGNORECASE):
                reps['no_conflict'] = True

            # Warranty period
            warranty_pattern = r'warrant(?:y|ies).*?(\d+)\s+(day|month|year)s?'
            match = re.search(warranty_pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                reps['warranty_period'] = f"{match.group(1)} {match.group(2)}s"
        else:
            reps['exists'] = False

        return reps

    def _extract_compliance_provisions(self, text: str) -> Dict[str, Any]:
        """Extract compliance and regulatory provisions"""
        compliance = {}

        # Regulatory compliance
        regulations = []

        if re.search(r'GDPR|General Data Protection Regulation', text, re.IGNORECASE):
            regulations.append('GDPR')

        if re.search(r'HIPAA|Health Insurance Portability', text, re.IGNORECASE):
            regulations.append('HIPAA')

        if re.search(r'SOX|Sarbanes-Oxley', text, re.IGNORECASE):
            regulations.append('SOX')

        if re.search(r'CCPA|California Consumer Privacy Act', text, re.IGNORECASE):
            regulations.append('CCPA')

        if re.search(r'PCI|Payment Card Industry', text, re.IGNORECASE):
            regulations.append('PCI-DSS')

        if regulations:
            compliance['regulations'] = regulations

        # Export controls
        if re.search(r'export\s+control', text, re.IGNORECASE):
            compliance['export_controls'] = True

        # Anti-corruption
        if re.search(r'(?:FCPA|Foreign Corrupt Practices|anti-corruption)', text, re.IGNORECASE):
            compliance['anti_corruption'] = True

        return compliance

    def _extract_signatures(self, text: str) -> List[Dict[str, Any]]:
        """Extract signature information"""
        signatures = []

        # Look for signature blocks
        sig_patterns = [
            r'(?:Signed|Executed)\s+by[:\s]+([A-Za-z\s.]+?)(?:\n|Title|Date)',
            r'By:\s+/s/\s+([A-Za-z\s.]+)',
            r'Signature:\s*_+\s+([A-Za-z\s.]+)',
        ]

        for pattern in sig_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                signature = {
                    'signatory': match.group(1).strip(),
                    'date': None,
                }

                # Try to find associated date
                date_pattern = r'Date:\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
                date_match = re.search(date_pattern, text[match.end():match.end() + 100])
                if date_match:
                    signature['date'] = date_match.group(1)

                signatures.append(signature)

        return signatures

    def _extract_amendment_provisions(self, text: str) -> Dict[str, Any]:
        """Extract amendment and modification provisions"""
        amendments = {}

        # Check if amendments are allowed
        if re.search(r'(?:may\s+(?:not\s+)?be|shall\s+(?:not\s+)?be)\s+amended', text, re.IGNORECASE):
            if re.search(r'may\s+not\s+be\s+amended|shall\s+not\s+be\s+amended', text, re.IGNORECASE):
                amendments['allowed'] = False
            else:
                amendments['allowed'] = True

        # Requirements for amendments
        if re.search(r'(?:written|in writing).*?amendment', text, re.IGNORECASE):
            amendments['requires_writing'] = True

        if re.search(r'mutual.*?(?:consent|agreement).*?(?:amendment|modification)', text, re.IGNORECASE):
            amendments['requires_mutual_consent'] = True

        # Count of amendments (if this is an amended agreement)
        amendment_count_pattern = r'Amendment\s+(?:No\.|Number|#)\s*(\d+)'
        match = re.search(amendment_count_pattern, text, re.IGNORECASE)
        if match:
            amendments['count'] = int(match.group(1))
        else:
            amendments['count'] = 0

        return amendments

    def _extract_notice_provisions(self, text: str) -> Dict[str, Any]:
        """Extract notice and communication provisions"""
        notices = {}

        # Notice section
        notice_section = self._extract_section_content(text, 'Notices')
        if notice_section:
            notices['details'] = notice_section[:500]

            # Extract notice addresses
            addresses = re.findall(r'(?:to|at):\s*([A-Za-z0-9\s,\.#-]+(?:\d{5}))', notice_section)
            if addresses:
                notices['addresses'] = addresses

        # Notice methods
        methods = []
        if re.search(r'certified\s+mail', text, re.IGNORECASE):
            methods.append('certified_mail')
        if re.search(r'email', text, re.IGNORECASE):
            methods.append('email')
        if re.search(r'fax', text, re.IGNORECASE):
            methods.append('fax')
        if re.search(r'courier', text, re.IGNORECASE):
            methods.append('courier')

        if methods:
            notices['methods'] = methods

        return notices

    def _extract_section_content(self, text: str, section_name: str) -> Optional[str]:
        """Extract content of a specific section"""
        # Try to find section by header
        pattern = rf'{re.escape(section_name)}[:\.]?\s+(.+?)(?=\n\n|\d+\.|Article|Section|$)'
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _validate_agreement(self, parties: List[Dict], dates: Dict, signatures: List[Dict]) -> Dict[str, Any]:
        """Validate agreement completeness"""
        validation = {
            'is_complete': True,
            'warnings': [],
            'errors': []
        }

        # Check parties
        if not parties or len(parties) < 2:
            validation['errors'].append("Fewer than 2 parties identified")
            validation['is_complete'] = False

        # Check effective date
        if not dates.get('effective') and not dates.get('execution'):
            validation['warnings'].append("No effective date found")

        # Check signatures
        if not signatures:
            validation['warnings'].append("No signatures found")
        elif len(signatures) < len(parties):
            validation['warnings'].append(f"Only {len(signatures)} signature(s) for {len(parties)} parties")

        return validation

    def _parse_financial_value(self, value_str: str) -> Optional[float]:
        """Parse financial values"""
        if not value_str:
            return None

        cleaned = re.sub(r'[^\d,.\-()]+', '', str(value_str))
        is_negative = '(' in value_str and ')' in value_str
        cleaned = cleaned.replace(',', '').replace('(', '').replace(')', '')

        try:
            value = float(cleaned)
            return -abs(value) if is_negative else value
        except ValueError:
            return None


# Example usage:
"""
extractor = LegalAgreementExtractor()
result = extractor.extract(text, tables)

print(f"Agreement Type: {result['agreement_type']}")
print(f"Title: {result['title']}")
print(f"Effective Date: {result['effective_date']}")
print(f"\nParties ({result['party_count']}):")
for party in result['parties']:
    print(f"  - {party['name']} ({party['type']})")
print(f"\nContract Value: ${result['contract_value']:,.2f}" if result['contract_value'] else "")
print(f"Term: {result['initial_term']}")
print(f"Auto-Renewal: {result['auto_renewal']}")
print(f"\nGoverning Law: {result['governing_law']}")
print(f"Jurisdiction: {result['governing_jurisdiction']}")
print(f"\nConfidentiality: {result['confidentiality']}")
print(f"Termination for Convenience: {result['termination_for_convenience']}")
print(f"\nExecution Status: {result['execution_status']}")
print(f"Signatures: {len(result['signatures'])}")
"""