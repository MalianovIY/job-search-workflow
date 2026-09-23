"""Checks that public rules are configurable rather than candidate-specific."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_public_disclosure, pipeline


class DisclosurePolicyTests(unittest.TestCase):
    def test_missing_private_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(check_public_disclosure, "POLICY_PATH", Path(directory) / "missing.json"):
                violations = check_public_disclosure.find_violations("Example text")
        self.assertEqual(violations[0]["rule"], "policy-unavailable")

    def test_fictional_pattern_is_loaded_from_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps({
                "prohibited_patterns": [{"id": "demo", "pattern": "fictional detail"}],
            }), encoding="utf-8")
            with patch.object(check_public_disclosure, "POLICY_PATH", path):
                violations = check_public_disclosure.find_violations("A fictional detail appears here.")
        self.assertEqual([item["rule"] for item in violations], ["demo"])


class RoleProfileTests(unittest.TestCase):
    def test_title_exclusions_come_only_from_profile(self):
        with patch.object(pipeline, "load_json", return_value={"exclude_title_patterns": []}):
            self.assertIsNone(pipeline.explicit_role_mismatch("IT Support"))
        with patch.object(pipeline, "load_json", return_value={"exclude_title_patterns": ["^Example Role$"]}):
            self.assertEqual(pipeline.explicit_role_mismatch("Example Role"), "^Example Role$")


if __name__ == "__main__":
    unittest.main()
