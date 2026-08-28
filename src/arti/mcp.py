"""Dependency-free Model Context Protocol server for ARTI.

The STDIO transport uses one UTF-8 JSON-RPC message per line. Nothing except MCP
messages is written to stdout so this module can be launched directly by Codex.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from . import __version__
from .full_system import (
    check_full_system_requirements,
    get_full_system_job,
    prepare_full_system_simulation,
    read_full_system_log,
    run_full_system_simulation,
    stop_full_system_job,
)
from .parser import ParseError
from .service import generate_from_config, inspect_rtl


SERVER_NAME = "arti-rtl"
PROTOCOL_VERSION = "2025-11-25"
INSTRUCTIONS = (
    "For full-system RTL simulation: inspect_rtl, generate_project when a standalone "
    "generated project is requested, then check_full_system_requirements with an "
    "integration YAML. If not ready, ask before calling prepare_full_system_simulation "
    "because it downloads/builds large dependencies. Start the boot with "
    "run_full_system_simulation, poll get_full_system_job, and inspect failures or final "
    "evidence with read_full_system_log. Stop persistent Debian jobs when no longer "
    "needed. Prefer absolute paths. Generated output directories must be absent or empty."
)

OPTIONS_SCHEMA = {
    "type": "object",
    "description": "Optional overrides passed to the ARTI Linux/QEMU harness.",
    "properties": {
        "work_dir": {"type": "string"},
        "qemu_src": {"type": "string"},
        "linux_src": {"type": "string"},
        "driver_ko": {"type": "string"},
        "driver_deps": {"type": "string"},
        "driver_manifest": {"type": "string"},
        "driver_marker": {"type": "string"},
        "timeout_seconds": {"type": "integer", "minimum": 1},
        "gpu_reference": {"type": "boolean"},
        "gpu_drm_test": {"type": "boolean"},
        "display": {"type": "boolean"},
        "qemu_display": {"type": "string"},
    },
    "additionalProperties": False,
}

FULL_SYSTEM_INPUT_PROPERTIES = {
    "config": {
        "type": "string",
        "description": (
            "Path to an integration YAML; defaults to the generic Linux integration profile."
        ),
    },
    "options": OPTIONS_SCHEMA,
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "inspect_rtl",
        "title": "Inspect RTL interface",
        "description": (
            "Parse an ANSI-style Verilog RTL file, identify its top-level ports, "
            "and infer AXI-Lite, AXI4, AHB, APB, or AXI-Stream signal mappings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "rtl": {
                    "type": "string",
                    "description": "Path to the Verilog RTL source file.",
                },
                "top": {
                    "type": "string",
                    "description": "Optional top module name; defaults to the first module.",
                },
            },
            "required": ["rtl"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "generate_project",
        "title": "Generate ARTI co-simulation project",
        "description": (
            "Generate a SystemC/Verilator and optional QEMU integration project from "
            "an ARTI YAML configuration. The output directory must be absent or empty."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "config": {
                    "type": "string",
                    "description": "Path to the ARTI YAML configuration file.",
                },
                "output": {
                    "type": "string",
                    "description": "Destination directory for generated files.",
                },
            },
            "required": ["config", "output"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "check_full_system_requirements",
        "title": "Check full-system simulation requirements",
        "description": (
            "Run the non-booting integration preflight for an ARTI Linux/QEMU profile. "
            "Reports missing RTL, QEMU, kernel, driver, and driver compatibility artifacts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": FULL_SYSTEM_INPUT_PROPERTIES,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "prepare_full_system_simulation",
        "title": "Prepare full-system simulation",
        "description": (
            "Start a background setup job that may install build tools and download/build "
            "QEMU, Linux, BusyBox, Debian, drivers, and the embedded Verilated RTL model. "
            "This can take a long time and use substantial disk and network resources."
        ),
        "inputSchema": {
            "type": "object",
            "properties": FULL_SYSTEM_INPUT_PROPERTIES,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    },
    {
        "name": "run_full_system_simulation",
        "title": "Run full-system simulation",
        "description": (
            "Start a background QEMU full-system job. test boots a minimal Linux image and "
            "checks pass markers; debian starts a persistent Debian development system. "
            "The tool refuses to start until the full-system preflight passes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **FULL_SYSTEM_INPUT_PROPERTIES,
                "mode": {
                    "type": "string",
                    "enum": ["test", "debian"],
                    "default": "test",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    },
    {
        "name": "get_full_system_job",
        "title": "Get full-system job status",
        "description": "Get status, exit code, and log size for a background ARTI job.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "read_full_system_log",
        "title": "Read full-system job log",
        "description": "Read the most recent lines from a background ARTI job log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "tail_lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "default": 200,
                },
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "stop_full_system_job",
        "title": "Stop full-system job",
        "description": "Terminate a running ARTI setup or QEMU full-system job.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _require_string(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string when provided")
    return value


def _call_tool(name: str, arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise McpError(-32602, "tool arguments must be an object")

    operations: dict[str, Callable[[], dict[str, Any]]] = {
        "inspect_rtl": lambda: inspect_rtl(
            _require_string(arguments, "rtl"),
            _optional_string(arguments, "top"),
        ),
        "generate_project": lambda: generate_from_config(
            _require_string(arguments, "config"),
            _require_string(arguments, "output"),
        ),
        "check_full_system_requirements": lambda: check_full_system_requirements(arguments),
        "prepare_full_system_simulation": lambda: prepare_full_system_simulation(arguments),
        "run_full_system_simulation": lambda: run_full_system_simulation(arguments),
        "get_full_system_job": lambda: get_full_system_job(arguments),
        "read_full_system_log": lambda: read_full_system_log(arguments),
        "stop_full_system_job": lambda: stop_full_system_job(arguments),
    }
    operation = operations.get(name)
    if operation is None:
        raise McpError(-32602, f"unknown tool: {name}")

    try:
        result = operation()
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2, ensure_ascii=False),
                }
            ],
            "structuredContent": result,
        }
    except (OSError, ValueError, ParseError) as error:
        return {
            "content": [{"type": "text", "text": f"ARTI error: {error}"}],
            "isError": True,
        }


def _dispatch(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise McpError(-32600, "invalid JSON-RPC request")
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})

    # Notifications never receive a response.
    if "id" not in message:
        return None
    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return {
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    if method == "server/discover":
        return {
            "protocolVersion": "2026-07-28",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            raise McpError(-32602, "tools/call requires a tool name")
        return _call_tool(params["name"], params.get("arguments", {}))
    raise McpError(-32601, f"method not found: {method}")


def _response(message: Any) -> dict[str, Any] | None:
    request_id = message.get("id") if isinstance(message, dict) else None
    try:
        result = _dispatch(message)
        if result is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except McpError as error:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": error.code, "message": error.message},
        }


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = _response(message)
        except json.JSONDecodeError as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"parse error: {error.msg}"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
