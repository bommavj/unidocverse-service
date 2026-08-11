"""
Email parser for .eml and .msg files
Extracts sender, recipient, subject, body, and attachments
"""

import logging
from pathlib import Path
from typing import Dict, Any, List
import email
from email import policy
from email.parser import BytesParser
import datetime

logger = logging.getLogger(__name__)


class EmailParser:
    """Parse email files (.eml, .msg)"""

    SUPPORTED_FORMATS = {'.eml', '.msg'}

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the file"""
        ext = Path(filename).suffix.lower()
        return ext in self.SUPPORTED_FORMATS

    def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        """Parse email file and extract metadata and content"""

        logger.info(f"📧 Parsing email: {filename}")

        ext = Path(filename).suffix.lower()

        try:
            if ext == '.eml':
                return self._parse_eml(file_path)
            elif ext == '.msg':
                return self._parse_msg(file_path)
            else:
                raise ValueError(f"Unsupported email format: {ext}")

        except Exception as e:
            logger.error(f"❌ Error parsing email: {e}")
            import traceback
            logger.error(traceback.format_exc())

            return {
                'text': '',
                'metadata': {'error': str(e)},
                'confidence': 0.0
            }

    def _parse_eml(self, file_path: str) -> Dict[str, Any]:
        """Parse .eml (RFC 822 email) file"""

        with open(file_path, 'rb') as f:
            msg = BytesParser(policy=policy.default).parse(f)

        # Extract metadata
        metadata = {
            'from': str(msg.get('From', '')),
            'to': str(msg.get('To', '')),
            'cc': str(msg.get('Cc', '')),
            'subject': str(msg.get('Subject', '')),
            'date': str(msg.get('Date', '')),
            'message_id': str(msg.get('Message-ID', '')),
        }

        # Extract body
        body_parts = []
        attachments = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition', ''))

                # Get body text
                if content_type == 'text/plain' and 'attachment' not in content_disposition:
                    try:
                        body_parts.append(part.get_content())
                    except:
                        pass

                # Track attachments
                elif 'attachment' in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        attachments.append({
                            'filename': filename,
                            'content_type': content_type,
                            'size': len(part.get_payload(decode=True) or b'')
                        })
        else:
            # Single part message
            try:
                body_parts.append(msg.get_content())
            except:
                pass

        body = '\n\n'.join(body_parts)

        # Build full text representation
        text_parts = [
            f"From: {metadata['from']}",
            f"To: {metadata['to']}",
            f"Subject: {metadata['subject']}",
            f"Date: {metadata['date']}",
            "",
            body
        ]

        if attachments:
            text_parts.append(f"\nAttachments: {len(attachments)}")
            for att in attachments:
                text_parts.append(f"  - {att['filename']} ({att['content_type']})")

        full_text = '\n'.join(text_parts)

        metadata['attachments'] = attachments
        metadata['attachment_count'] = len(attachments)

        logger.info(f"✅ Email parsed: {len(full_text)} chars, {len(attachments)} attachments")

        return {
            'text': full_text,
            'metadata': metadata,
            'confidence': 0.95
        }

    def _parse_msg(self, file_path: str) -> Dict[str, Any]:
        """Parse .msg (Outlook) file"""

        try:
            import extract_msg

            msg = extract_msg.Message(file_path)

            # Extract metadata
            metadata = {
                'from': msg.sender or '',
                'to': msg.to or '',
                'cc': msg.cc or '',
                'subject': msg.subject or '',
                'date': str(msg.date) if msg.date else '',
            }

            # Get body
            body = msg.body or ''

            # Track attachments
            attachments = []
            for att in msg.attachments:
                attachments.append({
                    'filename': att.longFilename or att.shortFilename,
                    'size': len(att.data) if hasattr(att, 'data') else 0
                })

            # Build full text
            text_parts = [
                f"From: {metadata['from']}",
                f"To: {metadata['to']}",
                f"Subject: {metadata['subject']}",
                f"Date: {metadata['date']}",
                "",
                body
            ]

            if attachments:
                text_parts.append(f"\nAttachments: {len(attachments)}")
                for att in attachments:
                    text_parts.append(f"  - {att['filename']}")

            full_text = '\n'.join(text_parts)

            metadata['attachments'] = attachments
            metadata['attachment_count'] = len(attachments)

            msg.close()

            logger.info(f"✅ MSG parsed: {len(full_text)} chars, {len(attachments)} attachments")

            return {
                'text': full_text,
                'metadata': metadata,
                'confidence': 0.95
            }

        except ImportError:
            logger.error("❌ extract-msg not installed. Install: pip install extract-msg")
            return {
                'text': '',
                'metadata': {'error': 'extract-msg library not installed'},
                'confidence': 0.0
            }