import os
import sys
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from config import settings
from services.observability_service import ObservabilityService

logger = logging.getLogger(__name__)


class BackupService:
    """
    SQLite database online backup and integrity verification service.
    Performs live atomic backups without interrupting read/write transactions.
    """

    @classmethod
    def get_source_db_path(cls) -> str:
        """Resolves absolute path to active SQLite database."""
        db_url = settings.DATABASE_URL
        if "sqlite:///" in db_url:
            rel_path = db_url.replace("sqlite:///", "")
            if os.path.isabs(rel_path):
                return rel_path
            backend_candidate = os.path.abspath(os.path.join(BACKEND_DIR, rel_path))
            if os.path.exists(backend_candidate):
                return backend_candidate
            parent_candidate = os.path.abspath(os.path.join(BACKEND_DIR, "..", rel_path))
            if os.path.exists(parent_candidate):
                return parent_candidate
            return backend_candidate
        return os.path.abspath(os.path.join(BACKEND_DIR, "soccer.db"))

    @classmethod
    def create_database_backup(cls, custom_dest_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes atomic online SQLite backup and verifies file integrity.
        """
        source_path = cls.get_source_db_path()
        if not os.path.exists(source_path):
            return {
                "status": "FAILED",
                "error": f"Source database file not found at {source_path}"
            }

        dest_dir = custom_dest_dir or settings.BACKUP_DIRECTORY
        os.makedirs(dest_dir, exist_ok=True)

        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_filename = f"soccer_backup_{timestamp_str}.db"
        backup_path = os.path.join(dest_dir, backup_filename)

        start_time = datetime.now(timezone.utc)

        try:
            # 1. Atomic SQLite online backup
            src_conn = sqlite3.connect(source_path)
            dest_conn = sqlite3.connect(backup_path)
            
            src_conn.backup(dest_conn)
            
            dest_conn.close()
            src_conn.close()

            # 2. Verify backup integrity
            verify_conn = sqlite3.connect(backup_path)
            cursor = verify_conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            integrity_result = cursor.fetchone()
            verify_conn.close()

            is_valid = integrity_result and integrity_result[0] == "ok"
            file_size_bytes = os.path.getsize(backup_path)

            res = {
                "status": "SUCCESS" if is_valid else "CORRUPTED",
                "backup_filename": backup_filename,
                "backup_path": backup_path,
                "size_bytes": file_size_bytes,
                "integrity_check": integrity_result[0] if integrity_result else "unknown",
                "created_at": start_time.isoformat()
            }

            ObservabilityService.log_event(
                "database_backup_completed",
                category="maintenance",
                severity="INFO" if is_valid else "ERROR",
                details={"backup_file": backup_filename, "size_bytes": file_size_bytes}
            )

            return res
        except Exception as ex:
            logger.error(f"Database backup failed: {ex}")
            return {
                "status": "FAILED",
                "error": str(ex),
                "created_at": start_time.isoformat()
            }

    @classmethod
    def list_backups(cls, custom_dest_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists available backup archives with size and timestamp metadata."""
        dest_dir = custom_dest_dir or settings.BACKUP_DIRECTORY
        if not os.path.exists(dest_dir):
            return []

        backups = []
        for fname in os.listdir(dest_dir):
            if fname.endswith(".db"):
                fpath = os.path.join(dest_dir, fname)
                st = os.stat(fpath)
                backups.append({
                    "filename": fname,
                    "size_bytes": st.st_size,
                    "created_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                })

        backups.sort(key=lambda x: x["created_at"], reverse=True)
        return backups
