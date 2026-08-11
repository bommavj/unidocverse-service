from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal

from app.parsers.extractors.base_extractor import BaseExtractor


class TermsOfServiceExtractor(BaseExtractor):
    """
    Extract data from Terms of Service (ToS) documents including:
    - Service description and scope
    - User rights and obligations
    - Acceptable use policies
    - Account terms and restrictions
    - Payment and subscription terms
    - Content ownership and licensing
    - Privacy and data usage
    - Disclaimers and limitations of liability
    - Termination and suspension
    - Dispute resolution
    - Modification and update policies
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Determine service type
        service_type = self._determine_service_type(text)

        # Extract basic metadata
        title = self._extract_title(text)
        version = self._extract_version(text)

        # Extract dates
        dates = self._extract_dates(text)

        # Extract service provider information
        provider = self._extract_provider_info(text)

        # Extract service description
        service_info = self._extract_service_info(text)

        # Extract account requirements
        account_terms = self._extract_account_terms(text)

        # Extract acceptable use policy
        acceptable_use = self._extract_acceptable_use(text)

        # Extract prohibited activities
        prohibited = self._extract_prohibited_activities(text)

        # Extract payment and subscription terms
        payment_terms = self._extract_payment_terms(text, tables)

        # Extract content ownership and licensing
        content_rights = self._extract_content_rights(text)

        # Extract intellectual property provisions
        ip_provisions = self._extract_ip_provisions(text)

        # Extract privacy and data provisions
        privacy = self._extract_privacy_provisions(text)

        # Extract disclaimers
        disclaimers = self._extract_disclaimers(text)

        # Extract limitation of liability
        liability = self._extract_liability_provisions(text)

        # Extract warranty disclaimers
        warranties = self._extract_warranty_disclaimers(text)

        # Extract termination provisions
        termination = self._extract_termination_provisions(text)

        # Extract dispute resolution
        dispute_resolution = self._extract_dispute_resolution(text)

        # Extract governing law
        governing_law = self._extract_governing_law(text)

        # Extract modification/update policy
        modifications = self._extract_modification_policy(text)

        # Extract contact information
        contact = self._extract_contact_info(text)

        # Extract age restrictions
        age_restrictions = self._extract_age_restrictions(text)

        # Extract DMCA/copyright provisions
        dmca = self._extract_dmca_provisions(text)

        # Extract third-party services/links
        third_party = self._extract_third_party_provisions(text)

        # Validation
        validation = self._validate_tos(dates, disclaimers, liability)

        return {
            # Metadata
            'document_type': 'terms_of_service',
            'service_type': service_type,
            'title': title,
            'version': version,
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Dates
            'effective_date': dates.get('effective'),
            'last_updated': dates.get('last_updated'),
            'last_modified': dates.get('last_updated'),  # Alias

            # Service Provider
            'provider': provider,
            'company_name': provider.get('name'),
            'company_address': provider.get('address'),

            # Service Information
            'service_description': service_info.get('description'),
            'service_scope': service_info.get('scope'),
            'service_availability': service_info.get('availability'),
            'geographic_restrictions': service_info.get('geographic_restrictions'),

            # Account Terms
            'account_requirements': account_terms,
            'minimum_age': account_terms.get('minimum_age'),
            'registration_required': account_terms.get('registration_required'),
            'account_security': account_terms.get('security_requirements'),

            # Acceptable Use
            'acceptable_use_policy': acceptable_use,
            'prohibited_activities': prohibited,
            'content_guidelines': acceptable_use.get('content_guidelines'),

            # Payment Terms
            'payment_terms': payment_terms,
            'subscription_model': payment_terms.get('subscription_model'),
            'pricing_info': payment_terms.get('pricing'),
            'refund_policy': payment_terms.get('refund_policy'),
            'auto_renewal': payment_terms.get('auto_renewal'),
            'cancellation_policy': payment_terms.get('cancellation'),

            # Content and IP Rights
            'content_rights': content_rights,
            'user_content_license': content_rights.get('user_license'),
            'platform_content_license': content_rights.get('platform_license'),
            'ip_provisions': ip_provisions,

            # Privacy and Data
            'privacy_provisions': privacy,
            'data_collection': privacy.get('data_collection'),
            'data_usage': privacy.get('data_usage'),
            'privacy_policy_reference': privacy.get('privacy_policy_link'),

            # Disclaimers and Liability
            'disclaimers': disclaimers,
            'warranty_disclaimers': warranties,
            'as_is_disclaimer': warranties.get('as_is'),
            'no_warranty': warranties.get('no_warranty'),
            'limitation_of_liability': liability,
            'liability_cap': liability.get('cap'),
            'excludes_consequential_damages': liability.get('excludes_consequential'),

            # Termination
            'termination_provisions': termination,
            'user_can_terminate': termination.get('user_termination'),
            'provider_can_terminate': termination.get('provider_termination'),
            'suspension_rights': termination.get('suspension'),
            'effect_of_termination': termination.get('effect'),

            # Dispute Resolution
            'dispute_resolution': dispute_resolution,
            'arbitration_required': dispute_resolution.get('arbitration_required'),
            'class_action_waiver': dispute_resolution.get('class_action_waiver'),
            'governing_law': governing_law.get('law'),
            'jurisdiction': governing_law.get('jurisdiction'),

            # Modification Policy
            'modification_policy': modifications,
            'can_modify_without_notice': modifications.get('without_notice'),
            'notification_method': modifications.get('notification_method'),
            'continued_use_acceptance': modifications.get('continued_use_acceptance'),

            # Contact
            'contact_email': contact.get('email'),
            'contact_address': contact.get('address'),
            'support_url': contact.get('support_url'),

            # Age and Eligibility
            'age_restrictions': age_restrictions,
            'coppa_compliant': age_restrictions.get('coppa_compliant'),

            # Copyright/DMCA
            'dmca_provisions': dmca,
            'dmca_agent': dmca.get('agent'),

            # Third Party
            'third_party_provisions': third_party,
            'third_party_links': third_party.get('links_disclaimer'),

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _determine_service_type(self, text: str) -> str:
        """Determine the type of service"""
        text_lower = text.lower()

        # SaaS/Software
        if any(keyword in text_lower for keyword in ['saas', 'software as a service', 'cloud service']):
            return 'saas'
        elif 'software' in text_lower:
            return 'software'

        # E-commerce
        elif any(keyword in text_lower for keyword in ['e-commerce', 'online store', 'marketplace']):
            return 'ecommerce'

        # Social media/platform
        elif any(keyword in text_lower for keyword in ['social network', 'social media', 'user-generated content']):
            return 'social_media'

        # Gaming
        elif any(keyword in text_lower for keyword in ['game', 'gaming', 'virtual items', 'in-game']):
            return 'gaming'

        # Mobile app
        elif any(keyword in text_lower for keyword in ['mobile app', 'application', 'ios', 'android']):
            return 'mobile_app'

        # Financial services
        elif any(keyword in text_lower for keyword in ['financial service', 'payment', 'banking', 'cryptocurrency']):
            return 'financial'

        # Healthcare
        elif any(keyword in text_lower for keyword in ['healthcare', 'medical', 'telemedicine', 'hipaa']):
            return 'healthcare'

        # Education
        elif any(keyword in text_lower for keyword in ['educational', 'learning', 'course', 'training']):
            return 'education'

        # Subscription service
        elif 'subscription' in text_lower:
            return 'subscription'

        # API/Developer platform
        elif any(keyword in text_lower for keyword in ['api', 'developer', 'integration']):
            return 'api_platform'

        return 'general'

    def _extract_title(self, text: str) -> Optional[str]:
        """Extract ToS title"""
        lines = text.split('\n')[:5]

        for line in lines:
            line = line.strip()
            if any(keyword in line.lower() for keyword in
                   ['terms of service', 'terms and conditions', 'user agreement', 'terms of use']):
                return line

        return None

    def _extract_version(self, text: str) -> Optional[str]:
        """Extract version number"""
        patterns = [
            r'Version\s+(\d+\.?\d*\.?\d*)',
            r'v(\d+\.?\d*\.?\d*)',
            r'Revision\s+(\d+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _extract_dates(self, text: str) -> Dict[str, Optional[str]]:
        """Extract relevant dates"""
        dates = {}

        # Effective date
        effective_patterns = [
            r'Effective\s+Date:?\s*(\w+\s+\d{1,2},?\s+\d{4})',
            r'Effective:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'effective as of\s+(\w+\s+\d{1,2},?\s+\d{4})',
        ]

        for pattern in effective_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dates['effective'] = match.group(1).strip()
                break

        # Last updated
        updated_patterns = [
            r'Last\s+(?:Updated|Modified|Revised):?\s*(\w+\s+\d{1,2},?\s+\d{4})',
            r'Last\s+(?:Updated|Modified):?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Updated:?\s*(\w+\s+\d{1,2},?\s+\d{4})',
        ]

        for pattern in updated_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dates['last_updated'] = match.group(1).strip()
                break

        return dates

    def _extract_provider_info(self, text: str) -> Dict[str, Any]:
        """Extract service provider information"""
        provider = {}

        # Company name (usually early in document)
        lines = text.split('\n')[:20]
        for line in lines:
            if any(suffix in line for suffix in ['Inc.', 'LLC', 'Ltd', 'Corp', 'Limited', 'Corporation']):
                provider['name'] = line.strip()
                break

        # Address
        address_pattern = r'(?:located at|address|contact us at):\s*([^.]+?(?:\d{5}|[A-Z]{2}\s+\d{5}))'
        match = re.search(address_pattern, text, re.IGNORECASE)
        if match:
            provider['address'] = match.group(1).strip()

        # Website
        website_pattern = r'(?:www\.|https?://)([\w.-]+\.[\w.-]+)'
        match = re.search(website_pattern, text)
        if match:
            provider['website'] = match.group(0)

        return provider

    def _extract_service_info(self, text: str) -> Dict[str, Any]:
        """Extract service description and scope"""
        service = {}

        # Service description
        desc_patterns = [
            r'(?:provides?|offers?|operates?)\s+(.{100,500}?)(?:\.|;|\n\n)',
            r'(?:Service|Platform).*?(?:is|means)\s+(.{100,500}?)(?:\.|;|\n\n)',
        ]

        for pattern in desc_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                service['description'] = match.group(1).strip()[:500]
                break

        # Scope of service
        scope_section = self._extract_section_content(text, ['Scope', 'Services', 'Description of Service'])
        if scope_section:
            service['scope'] = scope_section[:500]

        # Availability
        if re.search(r'24/7|twenty-four hours', text, re.IGNORECASE):
            service['availability'] = '24/7'
        elif re.search(r'best efforts?', text, re.IGNORECASE):
            service['availability'] = 'Best efforts'

        # No guarantee of availability
        if re.search(r'(?:no|not)\s+guarantee.*?(?:availability|uptime|accessibility)', text, re.IGNORECASE):
            service['availability_guaranteed'] = False

        # Geographic restrictions
        geo_patterns = [
            r'(?:not available|restricted|prohibited)\s+(?:in|to)\s+([A-Za-z\s,]+)',
            r'(?:available only|solely available)\s+(?:in|to)\s+([A-Za-z\s,]+)',
        ]

        for pattern in geo_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                service['geographic_restrictions'] = match.group(1).strip()
                break

        return service

    def _extract_account_terms(self, text: str) -> Dict[str, Any]:
        """Extract account creation and management terms"""
        account = {}

        # Registration required
        if re.search(r'must\s+(?:create|register|sign up for)\s+an?\s+account', text, re.IGNORECASE):
            account['registration_required'] = True
        else:
            account['registration_required'] = False

        # Minimum age
        age_patterns = [
            r'(?:at least|minimum age of|must be)\s+(\d+)\s+years',
            r'(\d+)\s+years of age or older',
        ]

        for pattern in age_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                account['minimum_age'] = int(match.group(1))
                break

        # Account security requirements
        security_requirements = []

        if re.search(r'strong password', text, re.IGNORECASE):
            security_requirements.append('strong_password')
        if re.search(r'two-factor|2fa|multi-factor', text, re.IGNORECASE):
            security_requirements.append('2fa_available')
        if re.search(r'confidential.*?password', text, re.IGNORECASE):
            security_requirements.append('password_confidentiality')

        if security_requirements:
            account['security_requirements'] = security_requirements

        # One account per person
        if re.search(r'one account per (?:person|user|individual)', text, re.IGNORECASE):
            account['one_account_per_user'] = True

        # Account sharing prohibited
        if re.search(r'(?:may not|shall not|prohibited).*?share.*?account', text, re.IGNORECASE):
            account['sharing_prohibited'] = True

        # Accurate information required
        if re.search(r'accurate.*?information|truthful.*?information', text, re.IGNORECASE):
            account['accurate_info_required'] = True

        return account

    def _extract_acceptable_use(self, text: str) -> Dict[str, Any]:
        """Extract acceptable use policy"""
        aup = {}

        # Acceptable use section
        aup_section = self._extract_section_content(text, [
            'Acceptable Use', 'User Conduct', 'Your Use', 'Permitted Use'
        ])
        if aup_section:
            aup['policy'] = aup_section[:1000]

        # Content guidelines
        guidelines_section = self._extract_section_content(text, [
            'Content Guidelines', 'User Content', 'Content Standards'
        ])
        if guidelines_section:
            aup['content_guidelines'] = guidelines_section[:500]

        # Specific requirements
        requirements = []

        if re.search(r'lawful purposes? only', text, re.IGNORECASE):
            requirements.append('lawful_use_only')
        if re.search(r'personal.*?non-commercial', text, re.IGNORECASE):
            requirements.append('non_commercial_use')
        if re.search(r'comply with.*?(?:laws?|regulations?)', text, re.IGNORECASE):
            requirements.append('legal_compliance')

        if requirements:
            aup['requirements'] = requirements

        return aup

    def _extract_prohibited_activities(self, text: str) -> List[str]:
        """Extract prohibited activities"""
        prohibited = []

        # Common prohibitions
        prohibitions_map = {
            'illegal_activity': r'illegal|unlawful|criminal',
            'harassment': r'harass|abuse|threaten|stalk',
            'hate_speech': r'hate speech|discriminatory|racist|sexist',
            'spam': r'spam|unsolicited|bulk messages',
            'hacking': r'hack|unauthorized access|breach security',
            'malware': r'virus|malware|trojan|worm',
            'impersonation': r'impersonat|misrepresent|falsely claim',
            'copyright_infringement': r'infringe.*?copyright|piracy|unauthorized copying',
            'scraping': r'scrape|crawl|automated access|bot',
            'reverse_engineering': r'reverse engineer|decompile|disassemble',
            'interference': r'interfere|disrupt|impair.*?service',
            'fraudulent_activity': r'fraud|deceptive|misleading',
            'minors_exploitation': r'child.*?(?:pornography|exploitation|abuse)',
            'violence': r'violent|graphic violence|gore',
            'self_harm': r'self-harm|suicide|self-injury',
        }

        for activity, pattern in prohibitions_map.items():
            if re.search(pattern, text, re.IGNORECASE):
                prohibited.append(activity)

        return prohibited

    def _extract_payment_terms(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract payment and subscription terms"""
        payment = {}

        # Subscription model
        if re.search(r'subscription|recurring', text, re.IGNORECASE):
            payment['subscription_model'] = True

            # Subscription period
            period_patterns = [
                r'(?:billed|charged)\s+(?:every\s+)?(\w+)',
                r'(\w+)\s+subscription',
            ]

            for pattern in period_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    period = match.group(1).lower()
                    if period in ['monthly', 'month']:
                        payment['billing_period'] = 'monthly'
                    elif period in ['yearly', 'annual', 'year']:
                        payment['billing_period'] = 'annual'
                    break

        # Pricing
        pricing = {}

        # Free tier
        if re.search(r'free (?:tier|plan|version)|freemium', text, re.IGNORECASE):
            pricing['has_free_tier'] = True

        # Trial period
        trial_pattern = r'(\d+)[-\s]day\s+(?:free\s+)?trial'
        match = re.search(trial_pattern, text, re.IGNORECASE)
        if match:
            pricing['trial_period'] = f"{match.group(1)} days"

        if pricing:
            payment['pricing'] = pricing

        # Auto-renewal
        if re.search(r'auto(?:matic(?:ally)?)?[\s-]renew', text, re.IGNORECASE):
            payment['auto_renewal'] = True

            # Can disable auto-renewal
            if re.search(r'(?:may|can)\s+(?:cancel|disable|turn off).*?auto[\s-]renew', text, re.IGNORECASE):
                payment['can_disable_auto_renewal'] = True

        # Refund policy
        refund_section = self._extract_section_content(text, ['Refund', 'Cancellation and Refund'])
        if refund_section:
            payment['refund_policy'] = refund_section[:500]

            # No refunds
            if re.search(r'(?:no|not).*?refund', refund_section, re.IGNORECASE):
                payment['no_refunds'] = True

            # Partial refunds
            if re.search(r'pro[\s-]?rata|partial refund', refund_section, re.IGNORECASE):
                payment['pro_rata_refunds'] = True

        # Cancellation
        cancel_section = self._extract_section_content(text, ['Cancellation', 'How to Cancel'])
        if cancel_section:
            payment['cancellation'] = cancel_section[:300]

        # Price changes
        if re.search(r'(?:may|can|reserve the right to)\s+change.*?(?:price|fee)', text, re.IGNORECASE):
            payment['can_change_prices'] = True

        # Payment methods
        payment_methods = []
        if re.search(r'credit card', text, re.IGNORECASE):
            payment_methods.append('credit_card')
        if re.search(r'paypal', text, re.IGNORECASE):
            payment_methods.append('paypal')
        if re.search(r'stripe', text, re.IGNORECASE):
            payment_methods.append('stripe')

        if payment_methods:
            payment['payment_methods'] = payment_methods

        return payment

    def _extract_content_rights(self, text: str) -> Dict[str, Any]:
        """Extract content ownership and licensing provisions"""
        rights = {}

        # User content ownership
        if re.search(r'you (?:retain|own).*?(?:rights?|ownership).*?(?:your\s+)?content', text, re.IGNORECASE):
            rights['user_retains_ownership'] = True

        # License granted to platform
        license_patterns = [
            r'you (?:grant|hereby grant).*?(?:license|right).*?(?:to|for)\s+(?:us|[\w\s]+)',
            r'(?:license|right).*?user content',
        ]

        for pattern in license_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                license_text = match.group(0)

                # License scope
                if 'worldwide' in license_text.lower():
                    rights['license_scope'] = 'worldwide'

                if 'non-exclusive' in license_text.lower():
                    rights['license_exclusive'] = False
                elif 'exclusive' in license_text.lower():
                    rights['license_exclusive'] = True

                if 'royalty-free' in license_text.lower():
                    rights['royalty_free'] = True

                if 'perpetual' in license_text.lower():
                    rights['license_duration'] = 'perpetual'
                elif 'term of' in license_text.lower():
                    rights['license_duration'] = 'term_limited'

                rights['user_license'] = license_text[:300]
                break

        # Platform content license to user
        platform_license_pattern = r'(?:we|platform).*?(?:grant|license).*?(?:you|user)'
        match = re.search(platform_license_pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            rights['platform_license'] = match.group(0)[:300]

        # Content removal rights
        if re.search(r'(?:may|can|reserve the right to)\s+(?:remove|delete|disable).*?content', text, re.IGNORECASE):
            rights['platform_can_remove_content'] = True

        # Content backup
        if re.search(r'(?:not|no).*?(?:responsible|liable).*?(?:backup|loss).*?content', text, re.IGNORECASE):
            rights['user_responsible_for_backup'] = True

        return rights

    def _extract_ip_provisions(self, text: str) -> Dict[str, Any]:
        """Extract intellectual property provisions"""
        ip = {}

        # Platform owns IP
        if re.search(r'(?:all|our).*?(?:intellectual property|IP|proprietary rights)', text, re.IGNORECASE):
            ip['platform_owns_ip'] = True

        # Trademarks
        if re.search(r'trademark|service mark', text, re.IGNORECASE):
            ip['trademark_provisions'] = True

        # Copyright
        if re.search(r'copyright|©', text, re.IGNORECASE):
            ip['copyright_provisions'] = True

        # Restrictions on use
        restrictions = []
        if re.search(r'(?:may not|shall not).*?(?:copy|reproduce)', text, re.IGNORECASE):
            restrictions.append('no_copying')
        if re.search(r'(?:may not|shall not).*?modify', text, re.IGNORECASE):
            restrictions.append('no_modification')
        if re.search(r'(?:may not|shall not).*?(?:distribute|redistribute)', text, re.IGNORECASE):
            restrictions.append('no_distribution')

        if restrictions:
            ip['restrictions'] = restrictions

        return ip

    def _extract_privacy_provisions(self, text: str) -> Dict[str, Any]:
        """Extract privacy and data provisions"""
        privacy = {}

        # Privacy policy reference
        policy_patterns = [
            r'Privacy Policy.*?(?:at|located at|available at)\s+(https?://[\S]+)',
            r'privacy policy.*?(www\.[\S]+)',
        ]

        for pattern in policy_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                privacy['privacy_policy_link'] = match.group(1)
                break

        # Data collection
        if re.search(r'collect.*?(?:data|information)', text, re.IGNORECASE):
            privacy['data_collection'] = True

            # Types of data
            data_types = []
            if re.search(r'personal\s+(?:information|data)', text, re.IGNORECASE):
                data_types.append('personal_information')
            if re.search(r'usage\s+(?:data|information)', text, re.IGNORECASE):
                data_types.append('usage_data')
            if re.search(r'cookies?', text, re.IGNORECASE):
                data_types.append('cookies')
            if re.search(r'(?:device|location)\s+(?:data|information)', text, re.IGNORECASE):
                data_types.append('device_location')

            if data_types:
                privacy['data_types'] = data_types

        # Data usage
        usage_purposes = []
        if re.search(r'(?:use|process).*?(?:to\s+)?provide.*?service', text, re.IGNORECASE):
            usage_purposes.append('service_provision')
        if re.search(r'(?:use|process).*?(?:to\s+)?improve', text, re.IGNORECASE):
            usage_purposes.append('service_improvement')
        if re.search(r'(?:use|process).*?marketing', text, re.IGNORECASE):
            usage_purposes.append('marketing')
        if re.search(r'(?:share|disclose).*?third part(?:y|ies)', text, re.IGNORECASE):
            usage_purposes.append('third_party_sharing')

        if usage_purposes:
            privacy['data_usage'] = usage_purposes

        # GDPR compliance
        if re.search(r'GDPR|General Data Protection Regulation', text, re.IGNORECASE):
            privacy['gdpr_compliant'] = True

        # CCPA compliance
        if re.search(r'CCPA|California Consumer Privacy Act', text, re.IGNORECASE):
            privacy['ccpa_compliant'] = True

        return privacy

    def _extract_disclaimers(self, text: str) -> List[str]:
        """Extract various disclaimers"""
        disclaimers = []

        # Service availability disclaimer
        if re.search(r'(?:may|can).*?(?:discontinue|suspend|modify).*?service', text, re.IGNORECASE):
            disclaimers.append('service_availability')

        # Content accuracy disclaimer
        if re.search(r'(?:no|not).*?(?:guarantee|warrant).*?accuracy', text, re.IGNORECASE):
            disclaimers.append('content_accuracy')

        # Third-party content disclaimer
        if re.search(r'(?:not|no).*?responsible.*?third[\s-]party', text, re.IGNORECASE):
            disclaimers.append('third_party_content')

        # Results disclaimer
        if re.search(r'(?:no|not).*?guarantee.*?results', text, re.IGNORECASE):
            disclaimers.append('results')

        # Medical/legal advice disclaimer
        if re.search(r'(?:not|does not constitute).*?(?:medical|legal).*?advice', text, re.IGNORECASE):
            disclaimers.append('professional_advice')

        return disclaimers

    def _extract_warranty_disclaimers(self, text: str) -> Dict[str, Any]:
        """Extract warranty disclaimers"""
        warranties = {}

        # AS IS disclaimer
        if re.search(r'(?:provided\s+)?["\']?AS IS["\']?|AS-IS', text, re.IGNORECASE):
            warranties['as_is'] = True

        # AS AVAILABLE
        if re.search(r'AS AVAILABLE', text, re.IGNORECASE):
            warranties['as_available'] = True

        # No warranty
        if re.search(r'(?:WITHOUT|NO).*?WARRANT(?:Y|IES)', text, re.IGNORECASE):
            warranties['no_warranty'] = True

        # Specific warranty disclaimers
        if re.search(r'(?:no|disclaim).*?warrant(?:y|ies).*?merchantability', text, re.IGNORECASE):
            warranties['no_warranty_merchantability'] = True

        if re.search(r'(?:no|disclaim).*?warrant(?:y|ies).*?fitness', text, re.IGNORECASE):
            warranties['no_warranty_fitness'] = True

        # Full disclaimer section
        warranty_section = self._extract_section_content(text, [
            'Disclaimer', 'Warranty Disclaimer', 'No Warranties'
        ])
        if warranty_section:
            warranties['full_disclaimer'] = warranty_section[:500]

        return warranties

    def _extract_liability_provisions(self, text: str) -> Dict[str, Any]:
        """Extract limitation of liability provisions"""
        liability = {}

        # Limitation exists
        if re.search(r'limitation\s+of\s+liability', text, re.IGNORECASE):
            liability['exists'] = True

        # Liability cap
        cap_pattern = r'(?:shall\s+not\s+exceed|limited\s+to).*?\$\s*([\d,]+\.?\d*)'
        match = re.search(cap_pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            liability['cap'] = self._parse_financial_value(match.group(1))

        # Cap relative to fees paid
        if re.search(r'(?:amount|fees)\s+(?:you\s+)?(?:paid|have paid)', text, re.IGNORECASE):
            liability['cap_relative_to_fees'] = True

        # Exclusion of consequential damages
        exclusions = []
        if re.search(r'(?:no|not).*?liable.*?(?:indirect|consequential|incidental|punitive|special)\s+damages', text,
                     re.IGNORECASE):
            liability['excludes_consequential'] = True
            exclusions.append('consequential')

        if re.search(r'(?:no|not).*?liable.*?loss\s+of\s+(?:profits|revenue|data)', text, re.IGNORECASE):
            exclusions.append('loss_of_profits')

        if exclusions:
            liability['excluded_damages'] = exclusions

        # Full liability section
        liability_section = self._extract_section_content(text, [
            'Limitation of Liability', 'Liability Limitation'
        ])
        if liability_section:
            liability['full_text'] = liability_section[:1000]

        return liability

    def _extract_termination_provisions(self, text: str) -> Dict[str, Any]:
        """Extract termination provisions"""
        termination = {}

        # User can terminate
        if re.search(r'(?:you|user).*?(?:may|can).*?(?:terminate|cancel|close).*?account', text, re.IGNORECASE):
            termination['user_termination'] = True

            # How to terminate
            how_to = self._extract_section_content(text, ['How to Cancel', 'Cancellation'])
            if how_to:
                termination['user_termination_method'] = how_to[:200]

        # Provider can terminate
        if re.search(
                r'(?:we|platform).*?(?:may|can|reserve the right).*?(?:terminate|suspend|disable).*?(?:your\s+)?account',
                text, re.IGNORECASE):
            termination['provider_termination'] = True

            # Grounds for termination
            grounds = []
            if re.search(r'terminat(?:e|ion).*?(?:for\s+)?(?:any\s+reason|at\s+any\s+time)', text, re.IGNORECASE):
                grounds.append('any_reason')
            if re.search(r'terminat(?:e|ion).*?violation', text, re.IGNORECASE):
                grounds.append('tos_violation')
            if re.search(r'terminat(?:e|ion).*?illegal', text, re.IGNORECASE):
                grounds.append('illegal_activity')

            if grounds:
                termination['termination_grounds'] = grounds

        # Suspension
        if re.search(r'(?:may|can).*?suspend', text, re.IGNORECASE):
            termination['suspension'] = True

        # Effect of termination
        effect_section = self._extract_section_content(text, ['Effect of Termination', 'Upon Termination'])
        if effect_section:
            termination['effect'] = effect_section[:300]

        # Data deletion
        if re.search(r'(?:delete|remove).*?(?:your\s+)?data.*?(?:upon|after).*?termination', text, re.IGNORECASE):
            termination['data_deleted_on_termination'] = True

        return termination

    def _extract_dispute_resolution(self, text: str) -> Dict[str, Any]:
        """Extract dispute resolution mechanisms"""
        dispute = {}

        # Arbitration
        if re.search(r'arbitration', text, re.IGNORECASE):
            dispute['arbitration_required'] = True

            # Binding arbitration
            if re.search(r'binding\s+arbitration', text, re.IGNORECASE):
                dispute['binding_arbitration'] = True

            # Arbitration provider
            if re.search(r'AAA|American Arbitration Association', text, re.IGNORECASE):
                dispute['arbitration_provider'] = 'AAA'
            elif re.search(r'JAMS', text, re.IGNORECASE):
                dispute['arbitration_provider'] = 'JAMS'

        # Class action waiver
        if re.search(r'(?:waive|no).*?(?:class|collective)\s+action', text, re.IGNORECASE):
            dispute['class_action_waiver'] = True

        # Jury trial waiver
        if re.search(r'waive.*?(?:right\s+to\s+)?(?:jury\s+)?trial', text, re.IGNORECASE):
            dispute['jury_trial_waiver'] = True

        # Informal resolution first
        if re.search(r'(?:first|before).*?(?:informal|direct).*?(?:resolution|negotiation)', text, re.IGNORECASE):
            dispute['informal_resolution_first'] = True

        # Limitation period for claims
        period_pattern = r'(?:bring|file).*?claim.*?within\s+(\d+)\s+(day|month|year)s?'
        match = re.search(period_pattern, text, re.IGNORECASE)
        if match:
            dispute['claim_limitation_period'] = f"{match.group(1)} {match.group(2)}s"

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
        jurisdiction_pattern = r'jurisdiction.*?(?:courts?\s+(?:of|in)|state\s+of)\s+([A-Za-z\s]+)'
        match = re.search(jurisdiction_pattern, text, re.IGNORECASE)
        if match:
            governing['jurisdiction'] = match.group(1).strip()

        return governing

    def _extract_modification_policy(self, text: str) -> Dict[str, Any]:
        """Extract policy on modifications to ToS"""
        modifications = {}

        # Can modify
        if re.search(
                r'(?:may|can|reserve the right to).*?(?:modify|change|update|amend).*?(?:these\s+)?(?:terms|agreement)',
                text, re.IGNORECASE):
            modifications['can_modify'] = True

            # Without notice
            if re.search(r'(?:modify|change|update).*?(?:without|no).*?notice', text, re.IGNORECASE):
                modifications['without_notice'] = True
            else:
                modifications['without_notice'] = False

            # How users are notified
            notification_methods = []
            if re.search(r'(?:notify|notice).*?(?:by\s+)?email', text, re.IGNORECASE):
                notification_methods.append('email')
            if re.search(r'post(?:ing|ed)?.*?(?:on|to).*?(?:website|service)', text, re.IGNORECASE):
                notification_methods.append('website_posting')
            if re.search(r'in-app\s+notification', text, re.IGNORECASE):
                notification_methods.append('in_app')

            if notification_methods:
                modifications['notification_method'] = notification_methods

        # Continued use = acceptance
        if re.search(r'continued\s+use.*?(?:constitutes|means).*?acceptance', text, re.IGNORECASE):
            modifications['continued_use_acceptance'] = True

        # Review requirement
        if re.search(r'(?:should|must).*?review.*?(?:periodically|regularly)', text, re.IGNORECASE):
            modifications['users_should_review'] = True

        # Effective date of changes
        if re.search(r'(?:changes|modifications).*?effective.*?(?:upon|immediately)', text, re.IGNORECASE):
            modifications['changes_effective_immediately'] = True

        return modifications

    def _extract_contact_info(self, text: str) -> Dict[str, Any]:
        """Extract contact information"""
        contact = {}

        # Email
        email_pattern = r'(?:contact|email|reach).*?([\w.-]+@[\w.-]+\.[\w]+)'
        match = re.search(email_pattern, text, re.IGNORECASE)
        if match:
            contact['email'] = match.group(1)

        # Address
        contact_section = self._extract_section_content(text, ['Contact', 'Contact Us', 'Questions'])
        if contact_section:
            contact['contact_info'] = contact_section[:300]

            # Extract address from contact section
            address_match = re.search(r'([A-Za-z0-9\s,\.#-]+(?:\d{5}))', contact_section)
            if address_match:
                contact['address'] = address_match.group(1)

        # Support URL
        support_pattern = r'(?:support|help).*?(https?://[\S]+)'
        match = re.search(support_pattern, text, re.IGNORECASE)
        if match:
            contact['support_url'] = match.group(1)

        return contact

    def _extract_age_restrictions(self, text: str) -> Dict[str, Any]:
        """Extract age restrictions and COPPA compliance"""
        age = {}

        # Minimum age
        age_patterns = [
            r'(?:must be|at least|minimum age)\s+(\d+)',
            r'(\d+)\s+years of age or older',
            r'not\s+(?:intended|directed)\s+(?:for|to).*?under\s+(\d+)',
        ]

        for pattern in age_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                age['minimum_age'] = int(match.group(1))
                break

        # COPPA compliance
        if re.search(r'COPPA|Children\'s Online Privacy Protection', text, re.IGNORECASE):
            age['coppa_compliant'] = True
            age['coppa_mentioned'] = True

        # Not directed to children
        if re.search(r'not\s+(?:intended|directed)\s+(?:for|to)\s+children', text, re.IGNORECASE):
            age['not_for_children'] = True

        # Parental consent required
        if re.search(r'parental\s+consent', text, re.IGNORECASE):
            age['parental_consent_required'] = True

        return age

    def _extract_dmca_provisions(self, text: str) -> Dict[str, Any]:
        """Extract DMCA/copyright provisions"""
        dmca = {}

        # DMCA compliance
        if re.search(r'DMCA|Digital Millennium Copyright Act', text, re.IGNORECASE):
            dmca['dmca_compliant'] = True

        # Copyright infringement policy
        policy_section = self._extract_section_content(text, [
            'Copyright', 'DMCA', 'Intellectual Property Infringement', 'Copyright Infringement'
        ])
        if policy_section:
            dmca['policy'] = policy_section[:500]

        # DMCA agent/contact
        agent_pattern = r'(?:DMCA|Copyright)\s+Agent.*?([^\n]+@[^\n]+)'
        match = re.search(agent_pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            dmca['agent'] = match.group(0)[:200]

        # Repeat infringer policy
        if re.search(r'repeat\s+infringer', text, re.IGNORECASE):
            dmca['repeat_infringer_policy'] = True

        return dmca

    def _extract_third_party_provisions(self, text: str) -> Dict[str, Any]:
        """Extract third-party service and link provisions"""
        third_party = {}

        # Third-party links disclaimer
        if re.search(r'third[\s-]party.*?(?:links?|websites?)', text, re.IGNORECASE):
            third_party['has_third_party_links'] = True

            # Not responsible for third-party content
            if re.search(r'(?:not|no).*?responsible.*?third[\s-]party', text, re.IGNORECASE):
                third_party['links_disclaimer'] = True

        # Third-party services
        if re.search(r'third[\s-]party\s+(?:services?|providers?)', text, re.IGNORECASE):
            third_party['uses_third_party_services'] = True

        # Analytics services
        analytics = []
        if re.search(r'Google Analytics', text, re.IGNORECASE):
            analytics.append('Google Analytics')
        if re.search(r'Facebook Pixel', text, re.IGNORECASE):
            analytics.append('Facebook Pixel')

        if analytics:
            third_party['analytics_services'] = analytics

        return third_party

    def _extract_section_content(self, text: str, section_names: List[str]) -> Optional[str]:
        """Extract content of a specific section"""
        for section_name in section_names:
            # Try numbered section
            pattern = rf'\d+\.\s+{re.escape(section_name)}[:\.]?\s+(.+?)(?=\n\d+\.|$)'
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()

            # Try header-based section
            pattern = rf'{re.escape(section_name)}[:\.]?\s+(.+?)(?=\n\n|$)'
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()

        return None

    def _validate_tos(self, dates: Dict, disclaimers: List, liability: Dict) -> Dict[str, Any]:
        """Validate ToS completeness"""
        validation = {
            'is_complete': True,
            'warnings': [],
            'recommended_sections': []
        }

        # Check for effective date
        if not dates.get('effective') and not dates.get('last_updated'):
            validation['warnings'].append("No effective date or last updated date found")

        # Check for key disclaimers
        if not disclaimers:
            validation['recommended_sections'].append("Add service disclaimers")

        # Check for liability limitation
        if not liability.get('exists'):
            validation['recommended_sections'].append("Add limitation of liability section")

        # Validation passes if no critical issues
        validation['is_complete'] = len(validation['warnings']) == 0

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
extractor = TermsOfServiceExtractor()
result = extractor.extract(text, tables)

print(f"Service Type: {result['service_type']}")
print(f"Effective Date: {result['effective_date']}")
print(f"Last Updated: {result['last_updated']}")
print(f"Company: {result['company_name']}")
print(f"\nAccount Requirements:")
print(f"  Minimum Age: {result['minimum_age']}")
print(f"  Registration Required: {result['registration_required']}")
print(f"\nProhibited Activities: {len(result['prohibited_activities'])}")
print(f"\nPayment Terms:")
print(f"  Subscription: {result.get('subscription_model')}")
print(f"  Auto-Renewal: {result.get('auto_renewal')}")
print(f"  Refunds: {'No' if result['payment_terms'].get('no_refunds') else 'Available'}")
print(f"\nContent Rights:")
print(f"  User Retains Ownership: {result['content_rights'].get('user_retains_ownership')}")
print(f"  License Granted: {'Yes' if result['content_rights'].get('user_license') else 'No'}")
print(f"\nLiability:")
print(f"  AS IS Disclaimer: {result.get('as_is_disclaimer')}")
print(f"  Consequential Damages Excluded: {result.get('excludes_consequential_damages')}")
print(f"\nDispute Resolution:")
print(f"  Arbitration Required: {result.get('arbitration_required')}")
print(f"  Class Action Waiver: {result.get('class_action_waiver')}")
print(f"  Governing Law: {result.get('governing_law')}")
print(f"\nModification Policy:")
print(f"  Can Modify: {result['modification_policy'].get('can_modify')}")
print(f"  Continued Use = Acceptance: {result.get('continued_use_acceptance')}")
"""