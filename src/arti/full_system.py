"""Full-system Linux/QEMU orchestration for the ARTI MCP server."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOB_ROOT = Path(os.environ.get("ARTI_MCP_JOB_DIR", "/tmp/arti-mcp-jobs"))
_LOCAL_PROCESSES: dict[str, subprocess.Popen[bytes]] = {}
SUPPORTED_OPTIONS = {
    "work_dir": "WORK_DIR",
    "qemu_src": "QEMU_SRC",
    "linux_src": "LINUX_SRC",
    "driver_ko": "DRIVER_KO",
    "driver_deps": "DRIVER_DEPS",
    "driver_manifest": "DRIVER_MANIFEST",
    "driver_marker": "DRIVER_MARKER",
    "timeout_seconds": "TIMEOUT",
    "gpu_reference": "GPU_REFERENCE",
    "gpu_drm_test": "GPU_DRM_TEST",
    "display": "ARTI_DISPLAY",
    "qemu_display": "QEMU_DISPLAY",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repository_root() -> Path:
    configured = os.environ.get("ARTI_REPO")
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(__file__).resolve().parents[2])
    for candidate in candidates:
        script = candidate / "examples/linux_arti_driver/run.sh"
        if script.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "ARTI source checkout not found; set ARTI_REPO to the repository root"
    )


def _profile(arguments: dict[str, Any]) -> Path:
    value = arguments.get("config")
    if value is None:
        return repository_root() / "examples/linux_arti_driver/integration.yaml"
    if not isinstance(value, str) or not value.strip():
        raise ValueError("config must be a non-empty string when provided")
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"integration config not found: {path}")
    return path


def _environment(arguments: dict[str, Any]) -> tuple[dict[str, str], Path]:
    env = os.environ.copy()
    profile = _profile(arguments)
    env["ARTI_DIR"] = str(repository_root())
    env["INTEGRATION_CONFIG"] = str(profile)
    options = arguments.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    unknown = sorted(set(options) - set(SUPPORTED_OPTIONS))
    if unknown:
        raise ValueError(f"unsupported options: {', '.join(unknown)}")
    for key, env_name in SUPPORTED_OPTIONS.items():
        if key not in options:
            continue
        value = options[key]
        if isinstance(value, bool):
            env[env_name] = "1" if value else "0"
        elif isinstance(value, (str, int)) and str(value):
            env[env_name] = str(value)
        else:
            raise ValueError(f"options.{key} must be a string, integer, or boolean")
    return env, profile


def check_full_system_requirements(arguments: dict[str, Any]) -> dict[str, Any]:
    """Run the repository's non-booting Linux/QEMU integration preflight."""
    env, profile = _environment(arguments)
    script = repository_root() / "examples/linux_arti_driver/check_integration.sh"
    try:
        result = subprocess.run(
            ["bash", str(script)],
            cwd=repository_root(),
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        return {
            "ready": result.returncode == 0,
            "exit_code": result.returncode,
            "config": str(profile),
            "output": result.stdout + result.stderr,
            "next_action": (
                "run_full_system_simulation"
                if result.returncode == 0
                else "prepare_full_system_simulation"
            ),
        }
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + (error.stderr or "")
        return {
            "ready": False,
            "config": str(profile),
            "output": output,
            "error": "preflight timed out after 120 seconds",
            "next_action": "inspect the preflight output before retrying",
        }


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _read_state(job_id: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(job_id, str) or not job_id or any(
        character not in "0123456789abcdef" for character in job_id
    ):
        raise ValueError("job_id must be a hexadecimal ARTI job identifier")
    state_path = JOB_ROOT / job_id / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"ARTI job not found: {job_id}")
    return state_path, json.loads(state_path.read_text(encoding="utf-8"))


def _start_job(
    action: str,
    command: list[str],
    env: dict[str, str],
    profile: Path,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    job_dir = JOB_ROOT / job_id
    state_path = job_dir / "state.json"
    log_path = job_dir / "job.log"
    state = {
        "job_id": job_id,
        "action": action,
        "status": "queued",
        "config": str(profile),
        "log": str(log_path),
        "created_at": _now(),
    }
    _write_state(state_path, state)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "arti.job_runner",
                str(state_path),
                "--",
                *command,
            ],
            cwd=repository_root(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _LOCAL_PROCESSES[job_id] = process
    return {
        **state,
        "pid": process.pid,
        "status": "running",
        "started_at": _now(),
        "next_action": "call get_full_system_job until status is succeeded or failed",
    }


def prepare_full_system_simulation(arguments: dict[str, Any]) -> dict[str, Any]:
    """Start the dependency, QEMU, Linux, driver, and embedded-model setup."""
    env, profile = _environment(arguments)
    script = repository_root() / "examples/linux_arti_driver/setup_env.sh"
    return _start_job("prepare", ["bash", str(script)], env, profile)


def run_full_system_simulation(arguments: dict[str, Any]) -> dict[str, Any]:
    """Start an automated test boot or persistent Debian full-system boot."""
    env, profile = _environment(arguments)
    mode = arguments.get("mode", "test")
    if mode not in ("test", "debian"):
        raise ValueError("mode must be test or debian")
    preflight = check_full_system_requirements(arguments)
    if not preflight["ready"]:
        raise ValueError(
            "full-system prerequisites are not ready; call "
            "prepare_full_system_simulation with user approval first. Preflight output:\n"
            + preflight.get("output", "")
        )
    script = repository_root() / "examples/linux_arti_driver/run.sh"
    return _start_job(f"run:{mode}", ["bash", str(script), mode], env, profile)


def _reap_local_process(job_id: str, wait: bool = False) -> None:
    process = _LOCAL_PROCESSES.get(job_id)
    if process is None:
        return
    if wait:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return
    elif process.poll() is None:
        return
    _LOCAL_PROCESSES.pop(job_id, None)


def get_full_system_job(arguments: dict[str, Any]) -> dict[str, Any]:
    state_path, state = _read_state(arguments.get("job_id"))
    _reap_local_process(
        state["job_id"],
        wait=state.get("status") in ("succeeded", "failed", "stopped"),
    )
    # The detached runner records its final state just before it exits. Re-read
    # after polling/reaping to avoid replacing a just-written result with unknown.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") == "running":
        pid = state.get("pid")
        try:
            os.kill(int(pid), 0)
        except (OSError, TypeError, ValueError):
            state["status"] = "unknown"
            state["message"] = "process exited without recording a final status"
            state["ended_at"] = _now()
            _write_state(state_path, state)
    log_path = Path(state["log"])
    state["log_size"] = log_path.stat().st_size if log_path.exists() else 0
    if state["status"] == "succeeded":
        state["next_action"] = "read_full_system_log to review the result"
    elif state["status"] in ("failed", "unknown"):
        state["next_action"] = "read_full_system_log to diagnose the failure"
    elif state["status"] in ("queued", "running"):
        state["next_action"] = "wait, then call get_full_system_job again"
    return state


def read_full_system_log(arguments: dict[str, Any]) -> dict[str, Any]:
    _, state = _read_state(arguments.get("job_id"))
    lines = arguments.get("tail_lines", 200)
    if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= 2000:
        raise ValueError("tail_lines must be an integer between 1 and 2000")
    log_path = Path(state["log"])
    content = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    selected = content.splitlines()[-lines:]
    return {
        "job_id": state["job_id"],
        "status": state["status"],
        "log": str(log_path),
        "tail_lines": len(selected),
        "output": "\n".join(selected),
    }


def stop_full_system_job(arguments: dict[str, Any]) -> dict[str, Any]:
    state_path, state = _read_state(arguments.get("job_id"))
    if state.get("status") != "running":
        return {**state, "stopped": False, "message": "job is not running"}
    pid = int(state["pid"])
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    _reap_local_process(state["job_id"], wait=True)
    state["status"] = "stopped"
    state["stopped"] = True
    state["ended_at"] = _now()
    _write_state(state_path, state)
    return state


def command_availability() -> dict[str, str | None]:
    """Small diagnostic helper used by tests and future frontends."""
    return {
        command: shutil.which(command)
        for command in ("bash", "python3", "verilator", "cmake", "ninja")
    }
