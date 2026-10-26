import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "release.py"
SHA = "a" * 40
DIGESTS = {"api": "sha256:" + "1" * 64, "web": "sha256:" + "2" * 64,
           "egress": "sha256:" + "3" * 64}


def invoke(*extra: str):
    return subprocess.run([sys.executable, str(SCRIPT), "--commit", SHA,
                           "--api", DIGESTS["api"], "--web", DIGESTS["web"],
                           "--egress", DIGESTS["egress"], *extra],
                          capture_output=True, text=True, check=False)


class ReleaseValidationTests(unittest.TestCase):
    def test_valid_immutable_release(self):
        result = invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"commit": SHA, **DIGESTS})

    def test_rejects_tags_instead_of_digests(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--commit", SHA,
                                 "--api", "latest", "--web", DIGESTS["web"],
                                 "--egress", DIGESTS["egress"]],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("latest", result.stdout)

    def test_rejects_shell_input_and_wrong_development_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder) / "development.json"
            evidence.write_text(json.dumps({"environment": "development", "result": "passed",
                                            "run_id": 123, "commit": SHA, **DIGESTS}))
            self.assertEqual(invoke("--source-run-id", "123", "--evidence", str(evidence)).returncode, 0)
            self.assertEqual(invoke("--source-run-id", "123;sh", "--evidence", str(evidence)).returncode, 2)
            record = json.loads(evidence.read_text())
            record["web"] = "sha256:" + "4" * 64
            evidence.write_text(json.dumps(record))
            self.assertEqual(invoke("--source-run-id", "123", "--evidence", str(evidence)).returncode, 2)
            record["web"] = DIGESTS["web"]
            record["result"] = "failed"
            evidence.write_text(json.dumps(record))
            self.assertEqual(invoke("--source-run-id", "123", "--evidence", str(evidence)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
