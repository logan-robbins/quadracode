from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field


def _default_workspace_root() -> str:
    return os.environ.get("WORKSPACE_ROOT") or os.environ.get("QUADRACODE_WORKSPACE_ROOT") or "/workspace"


class PropertyTestRequest(BaseModel):
    """Input schema for Hypothesis-driven property testing."""

    property_name: str = Field(..., description="Identifier for the property being validated.")
    module: str = Field(..., description="Python import path to the module under test (e.g., 'pkg.utils').")
    callable_name: str = Field(..., description="Name of the callable inside the module to exercise.")
    strategy_snippet: str = Field(
        ...,
        description="Python expression returning a Hypothesis strategy. `st`, `module`, and `target` are available.",
    )
    test_body: str = Field(
        ...,
        description="Python statements executed for each generated sample. Use `sample` and `target`; raise AssertionError on failure.",
    )
    preamble: Optional[str] = Field(
        default=None,
        description="Optional helper code (imports, fixtures) injected before the property definition.",
    )
    workspace_root: Optional[str] = Field(
        default=None,
        description="Workspace directory to run inside (defaults to /workspace).",
    )
    max_examples: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum Hypothesis examples to generate.",
    )
    deadline_ms: Optional[int] = Field(
        default=500,
        ge=100,
        description="Per-example deadline in milliseconds (set to null to disable).",
    )
    seed: Optional[int] = Field(
        default=None,
        description="Optional Hypothesis seed for deterministic reproduction.",
    )


def _indent(block: str, spaces: int = 4) -> str:
    return textwrap.indent(textwrap.dedent(block).strip(), " " * spaces)


def _build_property_script(params: PropertyTestRequest) -> str:
    preamble = textwrap.dedent(params.preamble or "").strip()
    preamble_block = f"{preamble}\n" if preamble else ""
    deadline = "None" if params.deadline_ms is None else str(params.deadline_ms)
    seed_setup = ""
    if params.seed is not None:
        seed_setup = f"""
import random
random.seed({params.seed})
try:
    import numpy as _np  # type: ignore[import-not-found]
    _np.random.seed({params.seed})
except Exception:
    pass
""".strip()
    test_body = _indent(params.test_body)

    script = f"""
import importlib
import json
import sys
import traceback
from pathlib import Path
from hypothesis import given, settings, strategies as st, HealthCheck, Verbosity

workspace_root = Path({params.workspace_root!r})
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

module = importlib.import_module({params.module!r})
target_callable = getattr(module, {params.callable_name!r})

{seed_setup}

{preamble_block}

failure_payload: dict[str, object] = {{}}
run_summary = {{"evaluated_examples": 0}}

strategy = eval({params.strategy_snippet!r}, {{"st": st, "module": module, "target": target_callable}})

@settings(
    max_examples={params.max_examples},
    deadline={deadline},
    suppress_health_check=[HealthCheck.too_slow],
    verbosity=Verbosity.quiet,
)
@given(strategy)
def property_test(sample):
    run_summary["evaluated_examples"] += 1
    failure_payload["last_sample"] = sample
    target = target_callable
{test_body}


def _serialize(value):
    try:
        json.dumps(value)
        return value
    except Exception:
        if isinstance(value, dict):
            return {{k: _serialize(v) for k, v in value.items()}}
        if isinstance(value, (list, tuple, set)):
            return [_serialize(v) for v in value]
        return repr(value)


def _build_result(status: str, **kwargs) -> dict[str, object]:
    payload = {{
        "status": status,
        "property_name": {params.property_name!r},
        "module": {params.module!r},
        "callable_name": {params.callable_name!r},
        "strategy": {params.strategy_snippet!r},
        "max_examples": {params.max_examples},
        "evaluated_examples": run_summary.get("evaluated_examples"),
        "seed": {params.seed!r},
    }}
    payload.update(kwargs)
    return payload


try:
    property_test()
    result = _build_result("passed")
except AssertionError as exc:  # property violation
    result = _build_result(
        "failed",
        failing_example=_serialize(failure_payload.get("last_sample")),
        failure_message=str(exc),
        traceback=traceback.format_exc(),
    )
except Exception as exc:  # unexpected harness error
    result = _build_result(
        "error",
        failure_message=str(exc),
        traceback=traceback.format_exc(),
        failing_example=_serialize(failure_payload.get("last_sample")),
    )

print(json.dumps(result, default=str))
""".strip()
    return script


def _run_property_script(script: str, workspace_root: str) -> Dict[str, Any]:
    workspace = Path(workspace_root or _default_workspace_root()).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix="-property-test.py",
        delete=False,
    ) as handle:
        handle.write(script)
        temp_path = Path(handle.name)
    try:
        proc = subprocess.run(
            ["python", str(temp_path)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        try:
            temp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass

    stdout = proc.stdout.strip()
    parsed: Dict[str, Any] | None = None
    if stdout:
        lines = stdout.splitlines()
        candidate = lines[-1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None

    if parsed is None:
        parsed = {
            "status": "error",
            "failure_message": "Unable to parse property test output.",
            "raw_stdout": stdout,
            "raw_stderr": proc.stderr,
        }

    parsed["returncode"] = proc.returncode
    if proc.stderr:
        parsed["stderr"] = proc.stderr.strip()
    if proc.stdout:
        parsed["stdout"] = proc.stdout.strip()
    return parsed


@tool(args_schema=PropertyTestRequest)
def generate_property_tests(
    property_name: str,
    module: str,
    callable_name: str,
    strategy_snippet: str,
    test_body: str,
    preamble: str | None = None,
    workspace_root: str | None = None,
    max_examples: int = 50,
    deadline_ms: int | None = 500,
    seed: int | None = None,
) -> str:
    """Run Hypothesis-driven property tests against a callable using adversarial inputs."""

    params = PropertyTestRequest(
        property_name=property_name,
        module=module,
        callable_name=callable_name,
        strategy_snippet=strategy_snippet,
        test_body=test_body,
        preamble=preamble,
        workspace_root=workspace_root or _default_workspace_root(),
        max_examples=max_examples,
        deadline_ms=deadline_ms,
        seed=seed,
    )
    raw_script = _build_property_script(params)
    result = _run_property_script(raw_script, params.workspace_root or _default_workspace_root())
    wrapped = {
        "tool": "generate_property_tests",
        "property_name": params.property_name,
        "result": result,
    }
    return json.dumps(wrapped, indent=2, sort_keys=True)


# Stable tool metadata
generate_property_tests.name = "generate_property_tests"
