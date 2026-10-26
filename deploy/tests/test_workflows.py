import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "deploy" / "validate_workflows.py"


class WorkflowSyntaxTests(unittest.TestCase):
    def test_both_real_workflows_parse(self):
        result = subprocess.run([sys.executable, str(VALIDATOR),
                                 str(ROOT / ".github/workflows/ci.yml"),
                                 str(ROOT / ".github/workflows/promote.yml")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_malformed_manual_workflow_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "promote.yml"
            source = (ROOT / ".github/workflows/promote.yml").read_text()
            broken.write_text(source.replace("workflow_dispatch:\n", "workflow_dispatch: [\n", 1))
            result = subprocess.run([sys.executable, str(VALIDATOR), str(broken)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid workflow", result.stderr)

    def test_duplicate_job_key_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "promote.yml"
            source = (ROOT / ".github/workflows/promote.yml").read_text()
            broken.write_text(source.replace("jobs:\n", "jobs:\n  verify-source: {}\n", 1))
            result = subprocess.run([sys.executable, str(VALIDATOR), str(broken)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)

    def test_missing_ssh_secret_fails_before_network(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("CD_SSH_")}
        result = subprocess.run(["bash", str(ROOT / "deploy/ssh_deploy.sh"),
                                 "development", "a" * 40, "sha256:" + "1" * 64,
                                 "sha256:" + "2" * 64, "sha256:" + "3" * 64,
                                 "123", "development-deployment.json"],
                                cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing environment host", result.stderr)
        self.assertNotIn("ssh:", result.stderr)


if __name__ == "__main__":
    unittest.main()
