"""The local responder launcher must only run the pinned Compose responder."""

import os
from pathlib import Path
import signal
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "incident-response" / "run.sh"


def _fake_tools(tmp_path: Path, *, fail: str = "") -> dict[str, str]:
    docker = tmp_path / "docker"
    docker.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *' build responder')
    if [[ "$FAKE_DOCKER_FAIL" == build ]]; then exit 7; fi ;;
  *' run --rm --no-deps -T responder')
    if [[ "$FAKE_DOCKER_FAIL" == run ]]; then exit 8; fi
    if [[ -e "$FAKE_DOCKER_LOCK" ]]; then printf 'OVERLAP\\n' >> "$FAKE_DOCKER_LOG"; fi
    touch "$FAKE_DOCKER_LOCK"
    /bin/sleep 0.03
    rm -f "$FAKE_DOCKER_LOCK" ;;
esac
""")
    docker.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_SLEEP_LOG"
exec /bin/sleep 0.03
""")
    sleep.chmod(0o755)
    return {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
            "FAKE_DOCKER_LOCK": str(tmp_path / "docker.lock"),
            "FAKE_DOCKER_FAIL": fail,
            "FAKE_SLEEP_LOG": str(tmp_path / "sleep.log")}


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def test_once_uses_only_optional_responder_service(tmp_path):
    env = _fake_tools(tmp_path)
    result = subprocess.run([str(LAUNCHER), "once"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    commands = _lines(tmp_path / "docker.log")
    assert len(commands) == 2
    for command in commands:
        assert f"--project-directory {ROOT}" in command
        assert f"-f {ROOT / 'compose.yaml'}" in command
        assert f"-f {ROOT / 'compose.observability.yaml'}" in command
        assert "--profile observability --profile responder" in command
    assert commands[0].endswith("build responder")
    assert commands[1].endswith("run --rm --no-deps -T responder")
    assert not (tmp_path / "docker.lock").exists()


def test_once_reports_build_and_scan_failure(tmp_path):
    for failure, expected in (("build", "Could not build"),
                              ("run", "Responder scan failed")):
        env = _fake_tools(tmp_path, fail=failure)
        result = subprocess.run([str(LAUNCHER), "once"], env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0
        assert expected in result.stderr


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_watch_is_serial_bounded_and_stops(tmp_path, stop_signal):
    env = _fake_tools(tmp_path)
    env["RUNGUARD_RESPONDER_INTERVAL_SECONDS"] = "5"
    process = subprocess.Popen([str(LAUNCHER), "watch"], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        deadline = time.monotonic() + 10
        while len(_lines(tmp_path / "sleep.log")) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(_lines(tmp_path / "sleep.log")) >= 2
        process.send_signal(stop_signal)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert "every 5 seconds" in stdout
        assert _lines(tmp_path / "sleep.log")[:2] == ["5", "5"]
        commands = _lines(tmp_path / "docker.log")
        assert sum(line.endswith("build responder") for line in commands) == 1
        assert sum(line.endswith("run --rm --no-deps -T responder") for line in commands) >= 2
        assert "OVERLAP" not in commands
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_rejects_commands_and_unbounded_intervals_without_docker(tmp_path):
    env = _fake_tools(tmp_path)
    for args, interval in ((["other"], None), (["once", "anything"], None),
                           (["watch"], "0"), (["watch"], "301"),
                           (["watch"], "5;id")):
        test_env = {**env}
        if interval is not None:
            test_env["RUNGUARD_RESPONDER_INTERVAL_SECONDS"] = interval
        result = subprocess.run([str(LAUNCHER), *args], env=test_env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 2
    assert not (tmp_path / "docker.log").exists()
