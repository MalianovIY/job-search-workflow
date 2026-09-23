"""Focused checks for the data-driven local dashboard."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import dashboard


class DashboardTests(unittest.TestCase):
    def test_list_uses_current_results_and_only_selected_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory)
            folder = results / "apply" / "company" / "record"
            folder.mkdir(parents=True)
            (folder / "metadata.json").write_text(json.dumps({
                "company": "Company",
                "title": "Title",
                "source_url": "https://example.invalid/item",
                "private_note": "must stay on disk",
            }), encoding="utf-8")
            (folder / "decision.json").write_text(json.dumps({
                "avg_match": 75,
                "red_team_match": 60,
                "reason": "must stay on disk",
            }), encoding="utf-8")
            (folder / "resume.pdf").write_bytes(b"pdf")
            with patch.object(dashboard, "RESULTS_DIR", results):
                jobs = dashboard.list_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["title"], "Title")
            self.assertEqual(jobs[0]["avg_match"], 75)
            self.assertTrue(jobs[0]["files"]["resume_pdf"])
            self.assertNotIn("private_note", json.dumps(jobs))
            self.assertNotIn("reason", json.dumps(jobs))

    def test_file_access_is_limited_to_result_files(self):
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory)
            folder = results / "apply" / "company" / "record"
            folder.mkdir(parents=True)
            allowed = folder / "resume.md"
            allowed.write_text("content", encoding="utf-8")
            (folder / "metadata.json").write_text("{}", encoding="utf-8")
            with patch.object(dashboard, "RESULTS_DIR", results):
                self.assertEqual(
                    dashboard.result_file("/api/job/apply/company/record/resume.md", "/api/job/", dashboard.TEXT_FILES),
                    allowed.resolve(),
                )
                self.assertIsNone(dashboard.result_file(
                    "/api/job/apply/company/record/metadata.json", "/api/job/", dashboard.TEXT_FILES,
                ))
                self.assertIsNone(dashboard.result_file(
                    "/api/job/apply/company/record/..%2Fmetadata.json", "/api/job/", dashboard.TEXT_FILES,
                ))


if __name__ == "__main__":
    unittest.main()
