"""
Archive parser for ZIP, TAR, RAR files
Extracts and processes files within archives
"""

import logging
from pathlib import Path
from typing import Dict, Any, List
import zipfile
import tarfile
import tempfile
import shutil

logger = logging.getLogger(__name__)


class ArchiveParser:
    """Parse archive files and process contents"""

    SUPPORTED_FORMATS = {'.zip', '.tar', '.gz', '.tar.gz', '.tgz'}

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the file"""
        ext = Path(filename).suffix.lower()
        # Handle double extensions like .tar.gz
        if filename.endswith('.tar.gz') or filename.endswith('.tgz'):
            return True
        return ext in self.SUPPORTED_FORMATS

    def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        """Parse archive and extract file list"""

        logger.info(f"📦 Parsing archive: {filename}")

        try:
            if filename.endswith('.zip'):
                return self._parse_zip(file_path)
            elif filename.endswith(('.tar', '.tar.gz', '.tgz', '.gz')):
                return self._parse_tar(file_path)
            else:
                raise ValueError(f"Unsupported archive format")

        except Exception as e:
            logger.error(f"❌ Error parsing archive: {e}")
            return {
                'text': '',
                'metadata': {'error': str(e)},
                'confidence': 0.0
            }

    def _parse_zip(self, file_path: str) -> Dict[str, Any]:
        """Parse ZIP archive"""

        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            file_list = zip_ref.namelist()

            # Get file info
            files_info = []
            total_size = 0

            for file_name in file_list:
                info = zip_ref.getinfo(file_name)
                files_info.append({
                    'name': file_name,
                    'size': info.file_size,
                    'compressed_size': info.compress_size,
                    'is_dir': file_name.endswith('/')
                })
                total_size += info.file_size

            # Create text summary
            text_parts = [
                f"ZIP Archive: {len(file_list)} files",
                f"Total size: {total_size:,} bytes",
                "",
                "Contents:"
            ]

            for file_info in files_info[:50]:  # List first 50
                if not file_info['is_dir']:
                    text_parts.append(f"  - {file_info['name']} ({file_info['size']:,} bytes)")

            if len(files_info) > 50:
                text_parts.append(f"\n  ... and {len(files_info) - 50} more files")

            full_text = '\n'.join(text_parts)

            logger.info(f"✅ ZIP parsed: {len(file_list)} files, {total_size:,} bytes")

            return {
                'text': full_text,
                'metadata': {
                    'file_count': len(file_list),
                    'total_size': total_size,
                    'files': files_info[:20]  # First 20 for metadata
                },
                'confidence': 0.85
            }

    def _parse_tar(self, file_path: str) -> Dict[str, Any]:
        """Parse TAR/TAR.GZ archive"""

        with tarfile.open(file_path, 'r:*') as tar:
            members = tar.getmembers()

            # Get file info
            files_info = []
            total_size = 0

            for member in members:
                files_info.append({
                    'name': member.name,
                    'size': member.size,
                    'is_dir': member.isdir()
                })
                total_size += member.size

            # Create text summary
            text_parts = [
                f"TAR Archive: {len(members)} files",
                f"Total size: {total_size:,} bytes",
                "",
                "Contents:"
            ]

            for file_info in files_info[:50]:
                if not file_info['is_dir']:
                    text_parts.append(f"  - {file_info['name']} ({file_info['size']:,} bytes)")

            if len(files_info) > 50:
                text_parts.append(f"\n  ... and {len(files_info) - 50} more files")

            full_text = '\n'.join(text_parts)

            logger.info(f"✅ TAR parsed: {len(members)} files, {total_size:,} bytes")

            return {
                'text': full_text,
                'metadata': {
                    'file_count': len(members),
                    'total_size': total_size,
                    'files': files_info[:20]
                },
                'confidence': 0.85
            }