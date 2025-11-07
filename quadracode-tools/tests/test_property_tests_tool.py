from __future__ import annotations

import json
from pathlib import Path

from quadracode_tools.tools.property_tests import generate_property_tests


def _setup_module(tmp_path: Path) -> None:
    pkg = tmp_path / "sample_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "math_ops.py").write_text(
        "\n".join(
            [
                "def clamp(value: int, lower: int = 0, upper: int = 10) -> int:",
                "    if value < lower:",
                "        return lower",
                "    if value > upper:",
                "        return upper",
                "    return value",
                "",
                "def always_positive(value: int) -> int:",
                "    return value + 1",
            ]
        )
    )


def test_generate_property_tests_pass(tmp_path: Path) -> None:
    _setup_module(tmp_path)
    payload = json.loads(
        generate_property_tests.invoke(
            {
                "property_name": "clamp-idempotent",
                "module": "sample_pkg.math_ops",
                "callable_name": "clamp",
                "strategy_snippet": "st.integers(min_value=-20, max_value=20)",
                "test_body": """
result = target(sample)
assert target(result) == result, "Clamp must be idempotent"
""",
                "workspace_root": str(tmp_path),
                "max_examples": 10,
            }
        )
    )

    result = payload["result"]
    assert result["status"] == "passed"
    assert result["property_name"] == "clamp-idempotent"
    assert result["evaluated_examples"] >= 1


def test_generate_property_tests_failure_reports_example(tmp_path: Path) -> None:
    _setup_module(tmp_path)
    payload = json.loads(
        generate_property_tests.invoke(
            {
                "property_name": "positive-only",
                "module": "sample_pkg.math_ops",
                "callable_name": "always_positive",
                "strategy_snippet": "st.integers(min_value=-5, max_value=5)",
                "test_body": """
result = target(sample)
assert result <= 0, "Expected non-positive result"
""",
                "workspace_root": str(tmp_path),
                "max_examples": 5,
                "seed": 42,
            }
        )
    )

    result = payload["result"]
    assert result["status"] == "failed"
    assert result["failing_example"] is not None
    assert "Expected non-positive result" in result["failure_message"]
