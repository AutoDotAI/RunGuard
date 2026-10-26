import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backup import backup  # noqa: E402
from state import read_record, record_deploy, record_rollback, rollback_target  # noqa: E402


def record(number: int, result="passed", environment="development"):
    return {
        "environment": environment, "commit": f"{number:x}" * 40,
        "api": "sha256:" + f"{number:x}" * 64,
        "web": "sha256:" + f"{number + 1:x}" * 64,
        "egress": "sha256:" + f"{number + 2:x}" * 64,
        "run_id": number, "started_at": "2026-10-26T00:00:00Z",
        "finished_at": "2026-10-26T00:01:00Z", "result": result,
    }


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.state = self.root / "development" / "state"
        self.database = self.root / "live.db"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
        self.connection.execute("INSERT INTO alembic_version VALUES ('rev1')")
        self.connection.execute("CREATE TABLE test_data(value TEXT)")
        self.connection.commit()

    def snapshot(self):
        return backup(self.database, self.root / "backups")

    def approve(self, target=record(1), revision="rev1", environment="development"):
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / "schema-compatibility.json"
        path.write_text(json.dumps({"environment": environment,
                                    "target_commit": target["commit"],
                                    "database_revision": revision,
                                    "compatible": True}))
        return path

    def test_sqlite_backup_includes_uncheckpointed_wal_and_is_private(self):
        self.connection.execute("PRAGMA wal_autocheckpoint=0")
        self.connection.execute("INSERT INTO test_data VALUES ('committed in WAL')")
        self.connection.commit()
        self.assertTrue(Path(str(self.database) + "-wal").exists())
        copy = self.snapshot()
        with sqlite3.connect(copy) as database:
            self.assertEqual(database.execute("SELECT value FROM test_data").fetchone(),
                             ("committed in WAL",))
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        self.assertEqual(copy.stat().st_mode & 0o777, 0o600)

    def test_backup_failure_does_not_create_success(self):
        with self.assertRaises(ValueError):
            backup(self.root / "missing.db", self.root / "backups")
        self.assertFalse((self.root / "backups").exists())

    def test_successful_history_and_failed_attempt_preserve_target(self):
        first, second = record(1), record(2)
        record_deploy(self.state, first)
        self.assertFalse((self.state / "previous.json").exists())
        record_deploy(self.state, {**second, "result": "failed"})
        self.assertFalse((self.state / "previous.json").exists())
        self.assertEqual(read_record(self.state / "current.json", "development")["commit"], first["commit"])
        record_deploy(self.state, second)
        self.assertEqual(read_record(self.state / "previous.json", "development")["commit"], first["commit"])
        record_deploy(self.state, {**record(3), "result": "failed"})
        self.assertEqual(read_record(self.state / "previous.json", "development")["commit"], first["commit"])
        self.assertEqual(read_record(self.state / "current.json", "development")["commit"], second["commit"])
        self.assertEqual(len((self.state / "attempts.jsonl").read_text().splitlines()), 4)

    def test_missing_corrupt_and_cross_environment_history_fail_closed(self):
        copy = self.snapshot()
        approval = self.approve()
        with self.assertRaises(OSError):
            rollback_target(self.state, "development", copy, approval)
        record_deploy(self.state, record(1))
        with self.assertRaises(OSError):
            rollback_target(self.state, "development", copy, approval)
        record_deploy(self.state, record(2))
        (self.state / "previous.json").write_text("not json")
        with self.assertRaises(json.JSONDecodeError):
            rollback_target(self.state, "development", copy, approval)
        (self.state / "previous.json").write_text(json.dumps({**record(1), "api": "latest"}))
        with self.assertRaises(ValueError):
            rollback_target(self.state, "development", copy, approval)
        (self.state / "previous.json").write_text(json.dumps(record(1, environment="production")))
        with self.assertRaises(ValueError):
            rollback_target(self.state, "development", copy, approval)

    def test_schema_compatibility_must_be_explicit_and_exact(self):
        record_deploy(self.state, record(1))
        record_deploy(self.state, record(2))
        copy = self.snapshot()
        approval = self.state / "schema-compatibility.json"
        with self.assertRaises(OSError):
            rollback_target(self.state, "development", copy, approval)
        self.approve(revision="wrong")
        with self.assertRaises(ValueError):
            rollback_target(self.state, "development", copy, approval)
        self.approve(environment="production")
        with self.assertRaises(ValueError):
            rollback_target(self.state, "development", copy, approval)
        self.approve()
        self.assertEqual(rollback_target(self.state, "development", copy, approval)["commit"], record(1)["commit"])

    def test_failed_rollback_keeps_history_and_success_swaps_records(self):
        record_deploy(self.state, record(1))
        record_deploy(self.state, record(2))
        record_rollback(self.state, "development", "failed")
        self.assertEqual(read_record(self.state / "current.json", "development")["commit"], record(2)["commit"])
        self.assertEqual(read_record(self.state / "previous.json", "development")["commit"], record(1)["commit"])
        record_rollback(self.state, "development", "passed")
        self.assertEqual(read_record(self.state / "current.json", "development")["commit"], record(1)["commit"])
        self.assertEqual(read_record(self.state / "previous.json", "development")["commit"], record(2)["commit"])

    def test_remote_health_and_authenticated_smoke_fail_without_success_record(self):
        for failing_step in ("up", "smoke"):
            with self.subTest(failing_step=failing_step):
                host = self.root / failing_step
                deploy_dir = host / "deploy"
                deploy_dir.mkdir(parents=True)
                for filename in ("remote_rollback.sh", "state.py", "release.py", "compose_overrides.py"):
                    shutil.copy2(ROOT / filename, deploy_dir / filename)
                (deploy_dir / "smoke.py").write_text("raise SystemExit(1)\n")
                (host / ".env.development").write_text(
                    "RUNGUARD_PUBLIC_HOST=demo.example.org\nRUNGUARD_ALLOWED_HOSTS=example.com\n"
                    "RUNGUARD_AUTH_SECRET=" + "s" * 64 + "\n"
                    "RUNGUARD_SMOKE_EMAIL=smoke@example.org\nRUNGUARD_SMOKE_PASSWORD=SmokePassword123!\n")
                (host / ".cd-security-approved").write_text("approved\n")
                state = host / "state"
                record_deploy(state, record(1))
                record_deploy(state, record(2))
                copy = self.snapshot()
                backups = host / "backups"
                backups.mkdir()
                shutil.copy2(copy, backups / "backup.db")
                (state / "schema-compatibility.json").write_text(json.dumps({
                    "environment": "development", "target_commit": record(1)["commit"],
                    "database_revision": "rev1", "compatible": True,
                }))
                bin_dir = host / "bin"
                bin_dir.mkdir()
                log = host / "docker.log"
                fake = bin_dir / "docker"
                proof = json.dumps({"environment": "development", "target_commit": record(1)["commit"],
                                    "database_revision": "rev1", "compatible": True})
                fake.write_text("#!/bin/sh\necho \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
                                "case \"$*\" in *check_compatibility.py*) echo '" + proof + "';; "
                                "run*) echo /backup/backup.db;; esac\n"
                                "case \"$*\" in *' up '*) test \"$FAKE_FAIL_STEP\" != up;; esac\n")
                fake.chmod(0o755)
                env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                       "FAKE_DOCKER_LOG": str(log), "FAKE_FAIL_STEP": failing_step}
                result = subprocess.run(["bash", "deploy/remote_rollback.sh", "development"],
                                        cwd=host, env=env, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(read_record(state / "current.json", "development")["commit"], record(2)["commit"])
                self.assertEqual(read_record(state / "previous.json", "development")["commit"], record(1)["commit"])
                self.assertEqual(json.loads((state / "latest-failure.json").read_text())["result"], "failed")
                self.assertIn("compose", log.read_text())

    def test_short_host_secret_stops_before_docker(self):
        host = self.root / "short-secret"
        (host / "deploy").mkdir(parents=True)
        shutil.copy2(ROOT / "remote_rollback.sh", host / "deploy" / "remote_rollback.sh")
        (host / ".cd-security-approved").write_text("approved\n")
        (host / ".env.development").write_text(
            "RUNGUARD_PUBLIC_HOST=demo.example.org\nRUNGUARD_ALLOWED_HOSTS=example.com\n"
            "RUNGUARD_AUTH_SECRET=too-short\nRUNGUARD_SMOKE_EMAIL=smoke@example.org\n"
            "RUNGUARD_SMOKE_PASSWORD=SmokePassword123!\n")
        bin_dir = host / "bin"
        bin_dir.mkdir()
        marker = host / "docker-called"
        fake = bin_dir / "docker"
        fake.write_text("#!/bin/sh\ntouch \"$DOCKER_MARKER\"\n")
        fake.chmod(0o755)
        env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
               "DOCKER_MARKER": str(marker)}
        result = subprocess.run(["bash", "deploy/remote_rollback.sh", "development"],
                                cwd=host, env=env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shorter than 32 bytes", result.stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
