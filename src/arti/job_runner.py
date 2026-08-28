"""Detached job supervisor used by :mod:`arti.full_system`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _write(path: Path, state: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[1] != "--":
        print("usage: python -m arti.job_runner STATE -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    state_path = Path(args[0])
    command = args[2:]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "running"
    state["pid"] = os.getpid()
    state["started_at"] = datetime.now(timezone.utc).isoformat()
    state["command"] = command
    _write(state_path, state)
    try:
        exit_code = subprocess.run(command, check=False).returncode
        state["exit_code"] = exit_code
        state["status"] = "succeeded" if exit_code == 0 else "failed"
        return exit_code
    except BaseException as error:
        state["status"] = "failed"
        state["error"] = f"{type(error).__name__}: {error}"
        return 1
    finally:
        state["ended_at"] = datetime.now(timezone.utc).isoformat()
        _write(state_path, state)


if __name__ == "__main__":
    raise SystemExit(main())
