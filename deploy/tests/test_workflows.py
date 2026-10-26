import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "deploy" / "validate_workflows.py"


class WorkflowSyntaxTests(unittest.TestCase):
    def test_development_downloads_and_validates_published_artifact_before_ssh(self):
        workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["deploy-development"]["steps"]
        download = next(i for i, step in enumerate(steps)
                        if step.get("uses", "").startswith("actions/download-artifact@"))
        validation = next(i for i, step in enumerate(steps)
                          if "--published-artifact release-images.json" in step.get("run", ""))
        ssh = next(i for i, step in enumerate(steps)
                   if "deploy/ssh_deploy.sh" in step.get("run", ""))
        self.assertEqual(steps[download]["with"]["name"], "release-images")
        self.assertLess(download, validation)
        self.assertLess(validation, ssh)

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

    def test_remote_deploy_reports_migration_health_and_auth_smoke_failures(self):
        old = {"environment": "development", "commit": "a" * 40,
               "api": "sha256:" + "1" * 64, "web": "sha256:" + "2" * 64,
               "egress": "sha256:" + "3" * 64, "run_id": 98,
               "started_at": "2026-10-26T00:00:00Z", "finished_at": "2026-10-26T00:01:00Z",
               "result": "passed"}
        for failure in ("up", "smoke"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                host = Path(folder)
                (host / "deploy").mkdir()
                for name in ("remote_deploy.sh", "release.py", "state.py"):
                    shutil.copy2(ROOT / "deploy" / name, host / "deploy" / name)
                (host / "deploy/smoke.py").write_text(
                    "import os\nfrom pathlib import Path\n"
                    "Path('smoke-called').write_text('authenticated smoke invoked')\n"
                    "raise SystemExit(1 if os.environ['FAKE_FAIL_STEP'] == 'smoke' else 0)\n")
                (host / ".env.development").write_text(
                    "RUNGUARD_PUBLIC_HOST=demo.example.org\n"
                    "RUNGUARD_ALLOWED_HOSTS=example.com\n"
                    "RUNGUARD_AUTH_SECRET=" + "s" * 64 + "\n"
                    "RUNGUARD_SMOKE_EMAIL=smoke@example.org\n"
                    "RUNGUARD_SMOKE_PASSWORD=SmokePassword123!\n")
                (host / ".cd-security-approved").write_text("approved\n")
                (host / "state").mkdir()
                (host / "state/current.json").write_text(json.dumps(old))
                (host / "bin").mkdir()
                fake = host / "bin/docker"
                fake.write_text("#!/bin/sh\necho \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
                                "case \"$*\" in *'volume inspect'*) exit 1;; "
                                "*' up -d '*) test \"$FAKE_FAIL_STEP\" != up;; esac\n")
                fake.chmod(0o755)
                docker_log = host / "docker.log"
                env = {**os.environ, "PATH": str(host / "bin") + os.pathsep + os.environ["PATH"],
                       "FAKE_DOCKER_LOG": str(docker_log), "FAKE_FAIL_STEP": failure}
                result = subprocess.run(
                    ["bash", "deploy/remote_deploy.sh", "development", "b" * 40,
                     "sha256:" + "4" * 64, "sha256:" + "5" * 64,
                     "sha256:" + "6" * 64, "123"], cwd=host, env=env,
                    capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("known-good release records retained", result.stderr)
                self.assertEqual(json.loads((host / "state/current.json").read_text()), old)
                self.assertFalse((host / "state/previous.json").exists())
                self.assertEqual(json.loads((host / "state/latest-failure.json").read_text())["result"], "failed")
                self.assertTrue(any("up -d --no-build --wait migrate api egress_guard worker web proxy" in line
                                    for line in docker_log.read_text().splitlines()))
                self.assertEqual((host / "smoke-called").exists(), failure == "smoke")


if __name__ == "__main__":
    unittest.main()
