import os
import sys
import unittest
import tempfile
import shutil

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.backup_service import BackupService


class TestBackupService(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_and_list_backup(self):
        """Creates an atomic online SQLite backup and checks listing."""
        res = BackupService.create_database_backup(custom_dest_dir=self.temp_dir)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertIn("backup_filename", res)
        self.assertGreater(res["size_bytes"], 0)
        self.assertEqual(res["integrity_check"], "ok")

        backups = BackupService.list_backups(custom_dest_dir=self.temp_dir)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["filename"], res["backup_filename"])


if __name__ == "__main__":
    unittest.main()
