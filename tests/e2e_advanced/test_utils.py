"""Unit tests for advanced E2E test utilities.

These fast checks still assume the docker-compose stack is up and healthy;
they simply focus on utility behavior without triggering full LLM flows.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from .utils.logging_framework import (
    create_test_log_directory,
    log_stream_snapshot,
    log_tool_call,
    log_turn,
)
from .utils.metrics_collector import MetricsCollector
from .utils.timeouts import (
    TimeoutManager,
    wait_for_condition,
    wait_for_condition_with_result,
)


class TestLoggingFramework:
    """Test logging framework utilities."""

    def test_create_test_log_directory(self, tmp_path):
        """Test log directory creation with timestamp."""
        log_dir = create_test_log_directory("test_example", base_dir=tmp_path)

        assert log_dir.exists()
        assert log_dir.is_dir()
        assert "test_example" in log_dir.name
        assert (log_dir / "test.log").exists()

    def test_log_turn(self, tmp_path):
        """Test turn logging with message and response."""
        log_dir = tmp_path / "test_logs"
        log_dir.mkdir()

        message = {
            "stream_id": "1234-0",
            "sender": "human",
            "recipient": "orchestrator",
            "message": "Hello",
        }
        response = {
            "stream_id": "1235-0",
            "sender": "orchestrator",
            "recipient": "human",
            "message": "Hi!",
        }

        log_turn(log_dir, 1, message, response, duration_ms=1500)

        turn_file = log_dir / "turn_001.json"
        assert turn_file.exists()

        with turn_file.open() as f:
            data = json.load(f)

        assert data["turn_number"] == 1
        assert data["duration_ms"] == 1500
        assert data["message"]["sender"] == "human"
        assert data["response"]["sender"] == "orchestrator"

    def test_log_stream_snapshot(self, tmp_path):
        """Test stream snapshot logging."""
        log_dir = tmp_path / "test_logs"
        log_dir.mkdir()

        entries = [
            ("1234-0", {"sender": "human", "message": "Hello"}),
            ("1235-0", {"sender": "orchestrator", "message": "Hi"}),
        ]

        log_stream_snapshot(log_dir, "qc:mailbox/orchestrator", entries)

        snapshot_file = log_dir / "qc_mailbox_orchestrator_snapshot.json"
        assert snapshot_file.exists()

        with snapshot_file.open() as f:
            data = json.load(f)

        assert data["stream"] == "qc:mailbox/orchestrator"
        assert data["entry_count"] == 2
        assert len(data["entries"]) == 2

    def test_log_tool_call(self, tmp_path):
        """Test tool call logging."""
        log_dir = tmp_path / "test_logs"
        log_dir.mkdir()

        inputs = {"command": "ls -la", "workspace_id": "ws-test"}
        outputs = {"stdout": "file1.py\nfile2.py", "exit_code": 0}

        log_tool_call(log_dir, "workspace_exec", inputs, outputs, duration_ms=250, success=True)

        # Find the tool call file (has timestamp in name)
        tool_files = list(log_dir.glob("tool_call_workspace_exec_*.json"))
        assert len(tool_files) == 1

        with tool_files[0].open() as f:
            data = json.load(f)

        assert data["tool_name"] == "workspace_exec"
        assert data["duration_ms"] == 250
        assert data["success"] is True
        assert data["inputs"]["command"] == "ls -la"


class TestMetricsCollector:
    """Test metrics collection system."""

    def test_initialization(self):
        """Test MetricsCollector initialization."""
        collector = MetricsCollector(test_name="test_example", run_id="run123")

        assert collector.test_name == "test_example"
        assert collector.run_id == "run123"
        assert collector.metrics["test_name"] == "test_example"
        assert collector.metrics["run_id"] == "run123"
        assert collector.metrics["false_stops"]["total"] == 0
        assert collector.metrics["humanclone"]["total_invocations"] == 0

    def test_record_false_stop(self):
        """Test recording false-stop events."""
        collector = MetricsCollector("test_example", "run123")

        proposal = {"stream_id": "1234-0", "message": "Task complete!"}

        collector.record_false_stop(
            proposal=proposal,
            detected_by="humanclone",
            stage="incomplete_implementation",
            recovery_time_ms=45000,
        )

        assert collector.metrics["false_stops"]["total"] == 1
        assert collector.metrics["false_stops"]["detected_by_humanclone"] == 1
        assert collector.metrics["false_stops"]["by_stage"]["incomplete_implementation"] == 1

        instances = collector.metrics["false_stops"]["instances"]
        assert len(instances) == 1
        assert instances[0]["stage"] == "incomplete_implementation"
        assert instances[0]["detected_by"] == "humanclone"
        assert instances[0]["recovery_time_ms"] == 45000

    def test_record_humanclone_invocation(self):
        """Test recording HumanClone interactions."""
        collector = MetricsCollector("test_example", "run123")

        proposal = {"message": "Task done"}
        trigger = {
            "exhaustion_mode": "TEST_FAILURE",
            "rationale": "Tests failing",
            "required_artifacts": ["passing_tests"],
        }

        collector.record_humanclone_invocation(
            proposal=proposal,
            trigger=trigger,
            outcome="rejection",
            latency_ms=15000,
            exhaustion_mode="TEST_FAILURE",
        )

        hc = collector.metrics["humanclone"]
        assert hc["total_invocations"] == 1
        assert hc["rejections"] == 1
        assert hc["acceptances"] == 0
        assert hc["trigger_exhaustion_modes"]["TEST_FAILURE"] == 1

        details = hc["trigger_details"]
        assert len(details) == 1
        assert details[0]["outcome"] == "rejection"
        assert details[0]["latency_ms"] == 15000

    def test_record_prp_transition(self):
        """Test recording PRP state transitions."""
        collector = MetricsCollector("test_example", "run123")

        collector.record_prp_transition("TEST", "CONCLUDE", valid=True, exhaustion_mode=None)
        collector.record_prp_transition("TEST", "HYPOTHESIZE", valid=True, exhaustion_mode="TEST_FAILURE")

        prp = collector.metrics["prp"]
        assert prp["transition_counts"]["TEST->CONCLUDE"] == 1
        assert prp["transition_counts"]["TEST->HYPOTHESIZE"] == 1
        assert prp["exhaustion_triggers"] == 1
        assert prp["invalid_transitions"] == 0

    def test_record_tool_call(self):
        """Test recording tool executions."""
        collector = MetricsCollector("test_example", "run123")

        collector.record_tool_call("workspace_exec", duration_ms=250.5, success=True)
        collector.record_tool_call("read_file", duration_ms=100.2, success=True)
        collector.record_tool_call("write_file", duration_ms=150.8, success=False)

        res = collector.metrics["resources"]
        assert res["tool_calls_total"] == 3
        assert res["tool_calls_success"] == 2
        assert res["tool_calls_failure"] == 1
        assert res["tool_calls_by_type"]["workspace_exec"] == 1
        assert res["tool_calls_by_type"]["read_file"] == 1

    def test_compute_derived_metrics(self):
        """Test computation of derived metrics."""
        collector = MetricsCollector("test_example", "run123")

        # Record some data
        collector.record_false_stop(
            {"stream_id": "1-0", "message": "Done"},
            detected_by="humanclone",
            stage="incomplete_implementation",
        )
        collector.record_humanclone_invocation(
            {"message": "Done"},
            {"exhaustion_mode": "TEST_FAILURE"},
            outcome="rejection",
        )
        collector.record_humanclone_invocation(
            {"message": "Done"},
            None,
            outcome="acceptance",
        )

        # Mark one rejection as correct
        collector.metrics["humanclone"]["correct_rejections"] = 1

        collector.compute_derived_metrics()

        fs = collector.metrics["false_stops"]
        assert fs["rate"] == 0.5  # 1 false-stop out of 2 proposals
        assert fs["detection_rate"] == 1.0  # 1 detected out of 1 total

        hc = collector.metrics["humanclone"]
        assert hc["rejection_rate"] == 0.5  # 1 rejection out of 2 invocations
        assert hc["precision"] == 1.0  # 1 correct out of 1 rejection
        assert hc["recall"] == 1.0  # 1 correct out of 1 false-stop
        assert hc["f1_score"] == 1.0

    def test_validate_consistency(self):
        """Test consistency validation."""
        collector = MetricsCollector("test_example", "run123")

        # Create inconsistent data
        collector.metrics["false_stops"]["total"] = 5
        collector.metrics["false_stops"]["detected_by_humanclone"] = 10  # Invalid: > total

        errors = collector.validate_consistency()
        assert len(errors) > 0
        assert any("detected_by_humanclone" in err for err in errors)

    def test_export(self, tmp_path):
        """Test metrics export to JSON."""
        collector = MetricsCollector("test_example", "run123")

        collector.record_false_stop(
            {"stream_id": "1-0"},
            detected_by="humanclone",
            stage="incomplete_implementation",
        )
        collector.compute_derived_metrics()

        output_path = tmp_path / "metrics.json"
        collector.export(output_path)

        assert output_path.exists()

        with output_path.open() as f:
            data = json.load(f)

        assert data["test_name"] == "test_example"
        assert data["run_id"] == "run123"
        assert data["false_stops"]["total"] == 1


class TestTimeoutWrappers:
    """Test timeout and polling utilities."""

    def test_wait_for_condition_success(self):
        """Test wait_for_condition with condition that becomes true."""
        call_count = 0

        def condition():
            nonlocal call_count
            call_count += 1
            return call_count >= 3

        result = wait_for_condition(condition, timeout=10, poll_interval=0.1)

        assert result is True
        assert call_count >= 3

    def test_wait_for_condition_timeout(self):
        """Test wait_for_condition with timeout."""

        def condition():
            return False

        result = wait_for_condition(condition, timeout=1, poll_interval=0.1)

        assert result is False

    def test_wait_for_condition_with_result_success(self):
        """Test wait_for_condition_with_result with successful result."""
        call_count = 0

        def get_result():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return {"status": "ready"}
            return None

        result = wait_for_condition_with_result(get_result, timeout=10, poll_interval=0.1)

        assert result is not None
        assert result["status"] == "ready"

    def test_wait_for_condition_with_result_timeout(self):
        """Test wait_for_condition_with_result with timeout."""

        def get_result():
            return None

        result = wait_for_condition_with_result(get_result, timeout=1, poll_interval=0.1)

        assert result is None

    def test_timeout_manager(self):
        """Test TimeoutManager context manager."""
        import time

        with TimeoutManager(timeout=5, operation="test operation") as tm:
            time.sleep(0.1)
            tm.checkpoint("step 1")
            time.sleep(0.1)
            tm.checkpoint("step 2")

        assert not tm.timed_out
        assert len(tm.checkpoints) == 2
        assert tm.elapsed() > 0.2

    def test_timeout_manager_remaining(self):
        """Test TimeoutManager remaining time calculation."""
        import time

        with TimeoutManager(timeout=5, operation="test operation") as tm:
            time.sleep(0.2)
            remaining = tm.remaining()
            assert 4.7 < remaining < 4.9


class TestMetricsCalculations:
    """Test metrics calculation helpers."""

    def test_calculate_rate(self):
        """Test safe rate calculation."""
        collector = MetricsCollector("test", "run")

        # Normal case
        assert collector._calculate_rate(3, 10) == 0.3

        # Zero denominator
        assert collector._calculate_rate(5, 0) == 0.0

        # Zero numerator
        assert collector._calculate_rate(0, 10) == 0.0

    def test_compute_f1_score(self):
        """Test F1 score computation."""
        collector = MetricsCollector("test", "run")

        # Normal case
        f1 = collector._compute_f1_score(0.8, 0.9)
        assert 0.84 < f1 < 0.85

        # Zero precision and recall
        assert collector._compute_f1_score(0.0, 0.0) == 0.0

        # One zero
        assert collector._compute_f1_score(0.8, 0.0) == 0.0
        assert collector._compute_f1_score(0.0, 0.9) == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

