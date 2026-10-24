"""
Dynamic Question Suggestion Service
Generates contextual questions based on document type and analysis

Author: Vijay Kumar Bomma
"""
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class QuestionSuggestionService:
    """
    Generates smart question suggestions based on document analysis
    Works with any document type
    """

    @staticmethod
    def generate_questions(
            doc_type: str,
            filename: str,
            analysis_results: Optional[Dict] = None,
            summary: Optional[str] = None
    ) -> List[str]:
        """
        Generate contextual questions for a document

        Args:
            doc_type: Document type (bank_statement, medical_record, etc.)
            filename: Document filename
            analysis_results: Analysis results from any analyzer
            summary: Document summary if available

        Returns:
            List of 5-7 suggested questions
        """

        # If we have analysis results, use them for smart questions
        if analysis_results and analysis_results.get('success'):
            return QuestionSuggestionService._generate_from_analysis(
                doc_type, analysis_results
            )

        # Fallback to type-based questions
        return QuestionSuggestionService._generate_from_type(doc_type, filename)

    @staticmethod
    def _generate_from_analysis(doc_type: str, analysis: Dict) -> List[str]:
        """Generate questions based on actual analysis results"""
        questions = []

        record_type = analysis.get('record_type', '')
        total_records = analysis.get('total_records', 0)
        statistics = analysis.get('statistics', {})
        insights = analysis.get('insights', [])

        # Medical Records
        if record_type == 'patient_treatment':
            questions.extend([
                f"How many patients are in this dataset? (Total: {total_records})",
                "What is the breakdown of patients by department?",
                "Which doctor has treated the most patients?",
                "What is the average treatment cost?",
                "What are the most common treatment types?",
                "Show me the age and gender distribution",
                "What is the average hospital stay duration?"
            ])

        # Bank Statements
        elif record_type == 'bank_statement' or 'transactions' in analysis:
            questions.extend([
                f"How many transactions are there? (Total: {total_records})",
                "What is the total of all deposits?",
                "What is the total of all withdrawals?",
                "What are the largest transactions?",
                "Show me spending by category",
                "What is the current balance?",
                "Summarize my spending patterns"
            ])

        # Lab Results
        elif record_type == 'lab_results':
            questions.extend([
                f"How many test results are there? (Total: {total_records})",
                "Are there any abnormal results?",
                "What tests were performed?",
                "What is the date range of these results?",
                "Summarize the key findings"
            ])

        # Sales/Invoice Data
        elif 'invoice' in doc_type or 'sales' in record_type:
            questions.extend([
                f"How many items/invoices are there? (Total: {total_records})",
                "What is the total amount?",
                "When is payment due?",
                "Who are the vendors/clients?",
                "What is the breakdown by category?"
            ])

        # Generic Structured Data
        elif total_records > 0:
            # Use insights to generate smart questions
            if insights:
                questions.append(f"Summarize the key insights ({len(insights)} found)")

            questions.extend([
                f"How many records are in this dataset? (Total: {total_records})",
                "What are the main categories or groups?",
                "Show me the distribution of key fields",
                "What are the summary statistics?",
                "What patterns or trends are visible?"
            ])

            # Add questions based on available statistics
            if statistics.get('numeric_summary'):
                numeric_fields = list(statistics['numeric_summary'].keys())
                if numeric_fields:
                    first_field = numeric_fields[0]
                    questions.append(f"What is the average {first_field}?")

            if statistics.get('categorical_summary'):
                categorical_fields = list(statistics['categorical_summary'].keys())
                if categorical_fields:
                    first_field = categorical_fields[0]
                    questions.append(f"Show me the breakdown by {first_field}")

        # Clinical Notes / Text Documents
        else:
            questions.extend([
                "What is this document about?",
                "Summarize the main points",
                "What are the key details?",
                "Are there any important dates or deadlines?",
                "Who are the main parties mentioned?"
            ])

        return questions[:7]  # Return max 7 questions

    @staticmethod
    def _generate_from_type(doc_type: str, filename: str) -> List[str]:
        """Fallback questions based on document type"""

        # Detect type from filename if doc_type is generic
        filename_lower = filename.lower()

        # Spreadsheets
        if doc_type == 'spreadsheet' or filename_lower.endswith(('.xlsx', '.xls', '.csv')):
            return [
                'What data is in this spreadsheet?',
                'How many rows and columns are there?',
                'What are the column headers?',
                'What is the total or sum of values?',
                'Show me key statistics',
                'Summarize the main data points'
            ]

        # Bank Statements
        elif doc_type == 'bank_statement' or 'bank' in filename_lower or 'statement' in filename_lower:
            return [
                'How many transactions are there?',
                'What is the total of deposits?',
                'What is the total of withdrawals?',
                'What is my current balance?',
                'What are my largest expenses?',
                'Summarize my spending'
            ]

        # Medical Records
        elif doc_type == 'medical_record' or 'patient' in filename_lower or 'medical' in filename_lower:
            return [
                'How many patients are in this dataset?',
                'What departments are included?',
                'What is the average treatment cost?',
                'Show me patient demographics',
                'What are the common diagnoses?',
                'Summarize the medical data'
            ]

        # Invoices
        elif doc_type == 'invoice' or 'invoice' in filename_lower:
            return [
                'What is the total amount?',
                'When is payment due?',
                'What services are included?',
                'Who is the vendor?',
                'What is the invoice number?'
            ]

        # Insurance
        elif 'insurance' in doc_type or 'policy' in filename_lower:
            return [
                'What is the total premium?',
                'What is covered under this policy?',
                'When does the policy expire?',
                'What are the coverage limits?',
                'Summarize the key terms'
            ]

        # Certificates
        elif doc_type == 'certificate':
            return [
                'Who is this certificate for?',
                'What is the certification about?',
                'When was it issued?',
                'Is there an expiration date?',
                'What institution issued this?'
            ]

        # Forms
        elif doc_type == 'form':
            return [
                'What type of form is this?',
                'What information is required?',
                'When is the deadline?',
                'Who should submit this?',
                'What are the key sections?'
            ]

        # Legal Documents
        elif doc_type == 'legal' or 'contract' in filename_lower or 'agreement' in filename_lower:
            return [
                'What is this agreement about?',
                'Who are the parties involved?',
                'What are the key terms?',
                'Are there any deadlines or dates?',
                'What are the obligations?',
                'Summarize the main clauses'
            ]

        # Reports
        elif doc_type == 'report' or 'report' in filename_lower:
            return [
                'What is this report about?',
                'What are the main findings?',
                'What data is included?',
                'What are the key recommendations?',
                'Summarize the conclusions'
            ]

        # Default for any document
        else:
            return [
                'What is this document about?',
                'Summarize the main points',
                'What are the key details?',
                'Are there any important dates?',
                'Who are the parties mentioned?',
                'What actions are required?'
            ]