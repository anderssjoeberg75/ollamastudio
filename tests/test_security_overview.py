"""Karaktäriseringstester för get_overview_snapshot (/api/security/overview).

Låser dagens kontrakt mot dashboarden. Observera: risknivån är i dag alltid
hårdkodad till "quiet" – testet dokumenterar det, det är inget godkännande.
"""
import sys
import unittest
from unittest import mock

from studio.harness.security_overview import get_overview_snapshot


class OverviewSnapshotTests(unittest.TestCase):
    def test_snapshot_reports_active_quiet_status_with_copy(self):
        snap = get_overview_snapshot()
        self.assertEqual(snap["status"], "active")
        self.assertEqual(snap["risk_level"], "quiet")
        self.assertEqual(snap["title"], "No immediate threat detected")
        self.assertTrue(snap["detail"])
        self.assertIsInstance(snap["timestamp"], float)

    def test_process_count_comes_from_psutil_when_available(self):
        fake = mock.MagicMock()
        fake.pids.return_value = [1, 2, 3]
        with mock.patch.dict(sys.modules, {"psutil": fake}):
            self.assertEqual(get_overview_snapshot()["monitored_processes"], 3)

    def test_process_count_is_zero_without_psutil(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            self.assertEqual(get_overview_snapshot()["monitored_processes"], 0)


if __name__ == "__main__":
    unittest.main()
