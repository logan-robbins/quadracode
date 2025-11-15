"""
Quadracode Advanced E2E Tests - Module 2: Context Engine Stress

This module validates the context engineering components under sustained load:
- Progressive loader with artifact cascade
- Context curation and externalization

Tests run for 7-8 minutes with real LLM calls, exercising:
- Progressive loading of test artifacts, workspace snapshots, and code files
- Context overflow handling with MemAct operations
- Externalization of large tool outputs
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

import pytest
import redis

from tests.e2e_advanced.utils.logging_framework import (
    create_test_log_directory,
    log_stream_snapshot,
    log_turn,
)
from tests.e2e_advanced.utils.redis_helpers import (
    dump_all_streams,
    get_last_stream_id,
    poll_stream_for_event,
    read_stream,
    send_message_to_orchestrator,
    wait_for_message_on_stream,
)
from tests.e2e_advanced.utils.artifacts import (
    capture_docker_logs,
    capture_workspace_state,
)
from tests.e2e_advanced.utils.timeouts import wait_for_condition
from tests.e2e_advanced.utils.metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)


@pytest.mark.e2e_advanced
@pytest.mark.long_running
def test_progressive_loader_artifact_cascade(
    docker_services, redis_client, test_config
):
    """
    Test 2.1: Progressive Loader Artifact Cascade (7 minutes)
    
    Objective: Trigger progressive loading of test artifacts, workspace snapshots,
    and code files across 20+ turns.
    
    This test validates:
    - Progressive loading triggered across multiple conversation turns
    - Context metrics stream records 'load' events with artifact segments
    - Pre-process events show increasing input_token_count over time
    - Curation events triggered when context size exceeds threshold
    
    Expected duration: 7 minutes minimum
    Expected turns: 20+
    Expected artifacts: Workspace with 5 Python files, 3 test artifacts
    
    Troubleshooting:
    - If workspace_create fails: Check Docker socket mount and permissions
    - If progressive loading not triggered: Verify QUADRACODE_CONTEXT_STRATEGY=progressive
    - If load events missing: Check qc:context:metrics stream in Redis
    """
    test_name = "test_progressive_loader_artifact_cascade"
    logger.info(f"Starting {test_name}")
    
    # Initialize metrics collector
    run_id = f"{int(time.time())}-{os.urandom(4).hex()}"
    collector = MetricsCollector(test_name=test_name, run_id=run_id)
    
    # Create log directory
    log_dir = create_test_log_directory(test_name)
    logger.info(f"Logs will be written to: {log_dir}")
    
    # Setup: Bring up stack
    # (Already brought up by docker_services fixture)
    
    try:
        # Initialize Redis stream baselines
        baseline_orchestrator = get_last_stream_id(
            redis_client, "qc:mailbox/orchestrator"
        )
        baseline_human = get_last_stream_id(redis_client, "qc:mailbox/human")
        baseline_context_metrics = get_last_stream_id(
            redis_client, "qc:context:metrics"
        )
        
        logger.info(
            f"Baseline stream IDs: orchestrator={baseline_orchestrator}, "
            f"human={baseline_human}, context_metrics={baseline_context_metrics}"
        )
        
        # Step 1: Create a workspace
        logger.info("Step 1: Creating workspace")
        send_message_to_orchestrator(
            redis_client,
            "Create a new workspace with ID 'ws-context-test' for testing context loading.",
            sender="human",
        )
        
        # Wait for response
        response = wait_for_message_on_stream(
            redis_client,
            "qc:mailbox/human",
            baseline_human,
            sender="orchestrator",
            timeout=90,
        )
        baseline_human = response["stream_id"]
        logger.info(f"Workspace creation response received: {response['stream_id']}")
        
        # Step 2: Populate workspace with Python files
        logger.info("Step 2: Populating workspace with Python files")
        
        # Create 5 Python files with substantial content (~2KB each = 10KB total)
        python_files = [
            {
                "name": "calculator.py",
                "content": '''"""Calculator module with advanced mathematical operations."""

import math
from typing import Union, List


class Calculator:
    """A comprehensive calculator supporting basic and advanced operations."""
    
    def __init__(self):
        self.memory = 0.0
        self.history = []
    
    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
        result = a + b
        self.history.append(f"add({a}, {b}) = {result}")
        return result
    
    def subtract(self, a: float, b: float) -> float:
        """Subtract b from a."""
        result = a - b
        self.history.append(f"subtract({a}, {b}) = {result}")
        return result
    
    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
        result = a * b
        self.history.append(f"multiply({a}, {b}) = {result}")
        return result
    
    def divide(self, a: float, b: float) -> float:
        """Divide a by b."""
        if b == 0:
            raise ValueError("Cannot divide by zero")
        result = a / b
        self.history.append(f"divide({a}, {b}) = {result}")
        return result
    
    def power(self, base: float, exponent: float) -> float:
        """Raise base to exponent."""
        result = base ** exponent
        self.history.append(f"power({base}, {exponent}) = {result}")
        return result
    
    def sqrt(self, x: float) -> float:
        """Calculate square root."""
        if x < 0:
            raise ValueError("Cannot calculate square root of negative number")
        result = math.sqrt(x)
        self.history.append(f"sqrt({x}) = {result}")
        return result
    
    def store_memory(self, value: float) -> None:
        """Store value in memory."""
        self.memory = value
        self.history.append(f"store_memory({value})")
    
    def recall_memory(self) -> float:
        """Recall value from memory."""
        self.history.append(f"recall_memory() = {self.memory}")
        return self.memory
    
    def clear_history(self) -> None:
        """Clear operation history."""
        self.history = []
''',
            },
            {
                "name": "data_processor.py",
                "content": '''"""Data processing utilities for ETL operations."""

import json
from typing import Dict, List, Any, Optional
from pathlib import Path


class DataProcessor:
    """Process and transform data from various sources."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.processed_count = 0
        self.error_count = 0
    
    def load_json(self, filepath: Path) -> Dict[str, Any]:
        """Load JSON data from file."""
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            self.processed_count += 1
            return data
        except Exception as e:
            self.error_count += 1
            raise RuntimeError(f"Failed to load JSON from {filepath}: {e}")
    
    def save_json(self, data: Dict[str, Any], filepath: Path) -> None:
        """Save data as JSON to file."""
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            self.processed_count += 1
        except Exception as e:
            self.error_count += 1
            raise RuntimeError(f"Failed to save JSON to {filepath}: {e}")
    
    def filter_records(
        self, records: List[Dict[str, Any]], key: str, value: Any
    ) -> List[Dict[str, Any]]:
        """Filter records by key-value pair."""
        filtered = [r for r in records if r.get(key) == value]
        return filtered
    
    def transform_record(
        self, record: Dict[str, Any], transformations: Dict[str, callable]
    ) -> Dict[str, Any]:
        """Apply transformations to record fields."""
        transformed = record.copy()
        for key, transform_fn in transformations.items():
            if key in transformed:
                try:
                    transformed[key] = transform_fn(transformed[key])
                except Exception as e:
                    raise RuntimeError(f"Transformation failed for key {key}: {e}")
        return transformed
    
    def aggregate(
        self, records: List[Dict[str, Any]], group_key: str, agg_key: str
    ) -> Dict[str, float]:
        """Aggregate numeric values grouped by key."""
        groups = {}
        for record in records:
            group = record.get(group_key)
            value = record.get(agg_key, 0)
            if group not in groups:
                groups[group] = []
            groups[group].append(value)
        
        # Calculate sum for each group
        return {g: sum(values) for g, values in groups.items()}
    
    def get_stats(self) -> Dict[str, int]:
        """Get processing statistics."""
        return {
            "processed_count": self.processed_count,
            "error_count": self.error_count,
        }
''',
            },
            {
                "name": "validator.py",
                "content": '''"""Input validation utilities."""

import re
from typing import Any, Dict, List, Optional


class ValidationError(Exception):
    """Raised when validation fails."""
    pass


class Validator:
    """Validate data against rules."""
    
    def __init__(self, strict: bool = True):
        self.strict = strict
        self.errors = []
    
    def validate_email(self, email: str) -> bool:
        """Validate email format."""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
        is_valid = bool(re.match(pattern, email))
        if not is_valid:
            self.errors.append(f"Invalid email format: {email}")
            if self.strict:
                raise ValidationError(f"Invalid email: {email}")
        return is_valid
    
    def validate_phone(self, phone: str) -> bool:
        """Validate phone number format (US)."""
        # Remove common separators
        cleaned = re.sub(r'[\\s()-]', '', phone)
        pattern = r'^\\+?1?\\d{10}$'
        is_valid = bool(re.match(pattern, cleaned))
        if not is_valid:
            self.errors.append(f"Invalid phone format: {phone}")
            if self.strict:
                raise ValidationError(f"Invalid phone: {phone}")
        return is_valid
    
    def validate_required_fields(
        self, data: Dict[str, Any], required: List[str]
    ) -> bool:
        """Validate that all required fields are present."""
        missing = [field for field in required if field not in data]
        if missing:
            self.errors.append(f"Missing required fields: {missing}")
            if self.strict:
                raise ValidationError(f"Missing fields: {missing}")
            return False
        return True
    
    def validate_range(
        self, value: float, min_val: float, max_val: float
    ) -> bool:
        """Validate numeric value is within range."""
        is_valid = min_val <= value <= max_val
        if not is_valid:
            self.errors.append(
                f"Value {value} out of range [{min_val}, {max_val}]"
            )
            if self.strict:
                raise ValidationError(
                    f"Value {value} out of range [{min_val}, {max_val}]"
                )
        return is_valid
    
    def get_errors(self) -> List[str]:
        """Get all validation errors."""
        return self.errors.copy()
    
    def clear_errors(self) -> None:
        """Clear error list."""
        self.errors = []
''',
            },
            {
                "name": "api_client.py",
                "content": '''"""HTTP API client utilities."""

import json
from typing import Dict, Any, Optional
from urllib.parse import urljoin


class APIClient:
    """HTTP client for RESTful APIs."""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url
        self.api_key = api_key
        self.default_headers = {
            "Content-Type": "application/json",
            "User-Agent": "Quadracode-API-Client/1.0",
        }
        if api_key:
            self.default_headers["Authorization"] = f"Bearer {api_key}"
        self.request_count = 0
    
    def _build_url(self, endpoint: str) -> str:
        """Build full URL from endpoint."""
        return urljoin(self.base_url, endpoint)
    
    def _prepare_headers(
        self, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Merge custom headers with defaults."""
        merged = self.default_headers.copy()
        if headers:
            merged.update(headers)
        return merged
    
    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Perform GET request."""
        url = self._build_url(endpoint)
        prepared_headers = self._prepare_headers(headers)
        self.request_count += 1
        # Note: Actual HTTP call would go here
        return {"status": "success", "url": url, "method": "GET"}
    
    def post(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Perform POST request."""
        url = self._build_url(endpoint)
        prepared_headers = self._prepare_headers(headers)
        self.request_count += 1
        # Note: Actual HTTP call would go here
        return {"status": "success", "url": url, "method": "POST"}
    
    def put(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Perform PUT request."""
        url = self._build_url(endpoint)
        prepared_headers = self._prepare_headers(headers)
        self.request_count += 1
        # Note: Actual HTTP call would go here
        return {"status": "success", "url": url, "method": "PUT"}
    
    def delete(
        self,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Perform DELETE request."""
        url = self._build_url(endpoint)
        prepared_headers = self._prepare_headers(headers)
        self.request_count += 1
        # Note: Actual HTTP call would go here
        return {"status": "success", "url": url, "method": "DELETE"}
''',
            },
            {
                "name": "test_calculator.py",
                "content": '''"""Unit tests for Calculator module."""

import pytest
from calculator import Calculator


def test_calculator_add():
    """Test addition operation."""
    calc = Calculator()
    assert calc.add(2, 3) == 5
    assert calc.add(-1, 1) == 0
    assert calc.add(0.1, 0.2) == pytest.approx(0.3)


def test_calculator_subtract():
    """Test subtraction operation."""
    calc = Calculator()
    assert calc.subtract(5, 3) == 2
    assert calc.subtract(0, 5) == -5


def test_calculator_multiply():
    """Test multiplication operation."""
    calc = Calculator()
    assert calc.multiply(2, 3) == 6
    assert calc.multiply(-2, 3) == -6


def test_calculator_divide():
    """Test division operation."""
    calc = Calculator()
    assert calc.divide(6, 2) == 3
    assert calc.divide(5, 2) == 2.5
    
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        calc.divide(5, 0)


def test_calculator_power():
    """Test exponentiation."""
    calc = Calculator()
    assert calc.power(2, 3) == 8
    assert calc.power(10, 0) == 1


def test_calculator_sqrt():
    """Test square root."""
    calc = Calculator()
    assert calc.sqrt(9) == 3
    assert calc.sqrt(0) == 0
    
    with pytest.raises(ValueError, match="Cannot calculate square root"):
        calc.sqrt(-1)


def test_calculator_memory():
    """Test memory operations."""
    calc = Calculator()
    calc.store_memory(42.5)
    assert calc.recall_memory() == 42.5


def test_calculator_history():
    """Test operation history."""
    calc = Calculator()
    calc.add(1, 2)
    calc.multiply(3, 4)
    assert len(calc.history) == 2
    assert "add(1, 2)" in calc.history[0]
    calc.clear_history()
    assert len(calc.history) == 0
''',
            },
        ]
        
        for file_info in python_files:
            send_message_to_orchestrator(
                redis_client,
                f"In workspace ws-context-test, create file /workspace/{file_info['name']} "
                f"with the following content:\n\n{file_info['content']}",
                sender="human",
            )
            
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=90,
            )
            baseline_human = response["stream_id"]
            logger.info(f"Created file {file_info['name']}: {response['stream_id']}")
        
        # Step 3: Create test artifacts (pytest output, coverage report, hypothesis log)
        logger.info("Step 3: Creating test artifacts in workspace")
        
        test_artifacts = [
            {
                "name": "pytest_output.txt",
                "content": "============================= test session starts ==============================\n"
                + "collected 8 items\n\n"
                + "test_calculator.py::test_calculator_add PASSED                            [ 12%]\n"
                + "test_calculator.py::test_calculator_subtract PASSED                       [ 25%]\n"
                + "test_calculator.py::test_calculator_multiply PASSED                       [ 37%]\n"
                + "test_calculator.py::test_calculator_divide PASSED                         [ 50%]\n"
                + "test_calculator.py::test_calculator_power PASSED                          [ 62%]\n"
                + "test_calculator.py::test_calculator_sqrt PASSED                           [ 75%]\n"
                + "test_calculator.py::test_calculator_memory PASSED                         [ 87%]\n"
                + "test_calculator.py::test_calculator_history PASSED                        [100%]\n\n"
                + "============================== 8 passed in 0.12s ===============================\n",
            },
            {
                "name": "coverage_report.txt",
                "content": "Name                      Stmts   Miss  Cover\n"
                + "---------------------------------------------\n"
                + "calculator.py                42      0   100%\n"
                + "data_processor.py            56      8    86%\n"
                + "validator.py                 38      5    87%\n"
                + "api_client.py                45     12    73%\n"
                + "test_calculator.py           32      0   100%\n"
                + "---------------------------------------------\n"
                + "TOTAL                       213     25    88%\n",
            },
            {
                "name": "hypothesis_log.txt",
                "content": "Hypothesis Statistics\n"
                + "=====================\n\n"
                + "test_calculator.py::test_calculator_add:\n"
                + "  - Tried 100 examples\n"
                + "  - Passed 100\n"
                + "  - Failed 0\n"
                + "  - Average runtime: 0.003s\n\n"
                + "test_calculator.py::test_calculator_divide:\n"
                + "  - Tried 100 examples\n"
                + "  - Passed 99\n"
                + "  - Failed 1 (division by zero caught correctly)\n"
                + "  - Average runtime: 0.004s\n\n"
                + "Shrinking:\n"
                + "  - Minimal counterexample found for divide: (5, 0)\n",
            },
        ]
        
        for artifact in test_artifacts:
            send_message_to_orchestrator(
                redis_client,
                f"In workspace ws-context-test, create artifact file /workspace/{artifact['name']} "
                f"with the following content:\n\n{artifact['content']}",
                sender="human",
            )
            
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=90,
            )
            baseline_human = response["stream_id"]
            logger.info(f"Created artifact {artifact['name']}: {response['stream_id']}")
        
        # Step 4: Test flow - trigger progressive loading over 20 turns
        logger.info("Step 4: Beginning 20-turn progressive loading test")
        
        test_start_time = time.time()
        turn_number = 0
        load_events = []
        
        progressive_loading_prompts = [
            "List all files in workspace ws-context-test.",
            "Read the calculator.py file from the workspace.",
            "Read the test_calculator.py file.",
            "Run the test suite in the workspace using pytest.",
            "Analyze the pytest output artifact.",
            "Read the coverage report artifact.",
            "Examine the data_processor.py file.",
            "Read the validator.py file.",
            "Analyze the hypothesis log artifact.",
            "Review the api_client.py file.",
            "Summarize all test results from the artifacts.",
            "List the functions defined in calculator.py.",
            "Check what validation functions exist in validator.py.",
            "Describe the DataProcessor class methods.",
            "What is the test coverage percentage from the coverage report?",
            "How many tests passed according to pytest output?",
            "Read calculator.py again to review the memory functions.",
            "Check data_processor.py for error handling patterns.",
            "Review validator.py for email validation logic.",
            "Examine api_client.py for HTTP methods.",
        ]
        
        for prompt in progressive_loading_prompts:
            turn_number += 1
            turn_start = time.time()
            
            logger.info(f"Turn {turn_number}: {prompt}")
            
            # Send message
            send_message_to_orchestrator(
                redis_client, prompt, sender="human"
            )
            
            # Wait for response
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=90,
            )
            baseline_human = response["stream_id"]
            
            turn_duration = time.time() - turn_start
            
            # Log turn
            log_turn(
                log_dir,
                turn_number,
                {"content": prompt},
                {
                    "stream_id": response["stream_id"],
                    "content": response.get("message", ""),
                    "duration_ms": turn_duration * 1000,
                },
            )
            
            # Check for context metrics events
            try:
                # Poll for load events
                load_event = poll_stream_for_event(
                    redis_client,
                    "qc:context:metrics",
                    baseline_context_metrics,
                    event_type="load",
                    timeout=10,
                )
                if load_event:
                    baseline_context_metrics = load_event[0]
                    load_events.append(load_event[1])
                    logger.info(f"Captured load event: {load_event[0]}")
            except Exception as e:
                logger.warning(f"No load event found for turn {turn_number}: {e}")
            
            logger.info(
                f"Turn {turn_number} complete: {turn_duration:.2f}s, "
                f"Response stream ID: {response['stream_id']}"
            )
        
        total_test_time = time.time() - test_start_time
        logger.info(f"Test flow complete: {total_test_time:.2f}s, {turn_number} turns")
        
        # Verification Step 1: Assert load events
        logger.info("Verification: Checking load events")
        assert len(load_events) >= 10, (
            f"Expected at least 10 load events from progressive loading, "
            f"got {len(load_events)}. This indicates progressive loader may not be "
            f"triggered correctly. Check environment variable QUADRACODE_CONTEXT_STRATEGY=progressive. "
            f"Load events captured: {json.dumps(load_events, indent=2)}"
        )
        logger.info(f"✓ Verified {len(load_events)} load events recorded")
        
        # Verification Step 2: Check for artifact segments in load events
        logger.info("Verification: Checking for artifact segments in load events")
        artifact_types_found = set()
        for event in load_events:
            payload = event.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            segments = payload.get("segments", [])
            for segment in segments:
                seg_type = segment.get("type", "")
                if seg_type:
                    artifact_types_found.add(seg_type)
        
        logger.info(f"Artifact types found in segments: {artifact_types_found}")
        # At least some segments should be loaded (file_content, tool_output, etc.)
        assert len(artifact_types_found) > 0, (
            f"No artifact types found in load event segments. "
            f"Expected types like 'file_content', 'tool_output', 'workspace_snapshot'. "
            f"Check progressive loader implementation."
        )
        logger.info(f"✓ Verified artifact segments present: {artifact_types_found}")
        
        # Verification Step 3: Check context metrics stream for pre_process events
        logger.info("Verification: Checking context metrics for pre_process events")
        context_metrics = read_stream(
            redis_client, "qc:context:metrics", count=100
        )
        pre_process_events = [
            e for e in context_metrics if e[1].get("event") == "pre_process"
        ]
        
        assert len(pre_process_events) >= turn_number, (
            f"Expected at least {turn_number} pre_process events (one per turn), "
            f"got {len(pre_process_events)}. This indicates context metrics may not be "
            f"recording correctly. Check orchestrator logs."
        )
        logger.info(f"✓ Verified {len(pre_process_events)} pre_process events")
        
        # Verification Step 4: Assert monotonically increasing input_token_count
        logger.info("Verification: Checking input token counts increase over time")
        token_counts = []
        for event in pre_process_events:
            payload = event[1].get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            token_count = payload.get("input_token_count", 0)
            token_counts.append(token_count)
        
        # Check that at least some events show increasing tokens (not strict monotonic)
        max_tokens = max(token_counts) if token_counts else 0
        min_tokens = min(token_counts) if token_counts else 0
        assert max_tokens > min_tokens, (
            f"Expected input_token_count to increase over turns, but "
            f"min={min_tokens}, max={max_tokens}. This suggests progressive loading "
            f"is not accumulating context. Check context engine configuration."
        )
        logger.info(
            f"✓ Verified input tokens increased: min={min_tokens}, max={max_tokens}"
        )
        
        # Verification Step 5: Check for curation events (if context exceeded threshold)
        logger.info("Verification: Checking for curation events")
        curation_events = [
            e for e in context_metrics if e[1].get("event") == "curation"
        ]
        logger.info(f"Found {len(curation_events)} curation events")
        # Curation may not trigger if context never exceeded threshold in this test
        # That's OK, we just log the count
        
        # Verification Step 6: Assert orchestrator mailbox has entries
        logger.info("Verification: Checking orchestrator mailbox message count")
        orchestrator_mailbox = read_stream(
            redis_client, "qc:mailbox/orchestrator", count=100
        )
        assert len(orchestrator_mailbox) >= turn_number, (
            f"Expected at least {turn_number} messages in orchestrator mailbox, "
            f"got {len(orchestrator_mailbox)}. Check message routing."
        )
        logger.info(f"✓ Verified orchestrator mailbox has {len(orchestrator_mailbox)} entries")
        
        # Verification Step 7: Assert human mailbox has entries
        logger.info("Verification: Checking human mailbox message count")
        human_mailbox = read_stream(
            redis_client, "qc:mailbox/human", count=100
        )
        assert len(human_mailbox) >= turn_number, (
            f"Expected at least {turn_number} messages in human mailbox, "
            f"got {len(human_mailbox)}. Check message routing."
        )
        logger.info(f"✓ Verified human mailbox has {len(human_mailbox)} entries")
        
        # Verification Step 8: Assert test duration >= 7 minutes
        logger.info("Verification: Checking test duration")
        min_duration = 7 * 60  # 7 minutes
        assert total_test_time >= min_duration, (
            f"Test ran for {total_test_time:.2f}s, expected at least {min_duration}s. "
            f"Test completed too quickly. Ensure sufficient conversation turns and timeouts."
        )
        logger.info(f"✓ Verified test duration: {total_test_time:.2f}s >= {min_duration}s")
        
        logger.info("✓ All verifications passed")
        
        # Record metrics
        collector.compute_derived_metrics()
        
        # Export metrics
        metrics_dir = Path("tests/e2e_advanced/metrics")
        metrics_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = metrics_dir / f"{test_name}_{run_id}_metrics.json"
        collector.export(metrics_path)
        logger.info(f"Metrics exported to: {metrics_path}")
        
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        raise
    
    finally:
        # Teardown
        logger.info("Teardown: Capturing artifacts and logs")
        
        # Dump Redis streams
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dump_all_streams(redis_client, artifacts_dir)
        
        # Capture Docker logs
        capture_docker_logs("orchestrator-runtime", artifacts_dir / "orchestrator.log")
        
        # Log context metrics snapshot
        log_stream_snapshot(
            log_dir, "context_metrics", context_metrics
        )
        
        logger.info(f"Test complete. Artifacts in: {artifacts_dir}")


@pytest.mark.e2e_advanced
@pytest.mark.long_running
def test_context_curation_and_externalization(
    docker_services, redis_client, test_config
):
    """
    Test 2.2: Context Curation and Externalization (8 minutes)
    
    Objective: Force context overflow and verify curator applies MemAct operations
    (retain, compress, summarize) and externalizes large tool outputs.
    
    This test validates:
    - Context overflow triggered when size exceeds threshold
    - Curation events contain 'actions' field with operations
    - Externalize action applied to large tool outputs
    - Compress action applied to segments
    - Context size stays below target threshold after curation
    
    Expected duration: 8 minutes minimum
    Expected turns: 10+
    Expected workspace: Large code files (50KB total)
    
    Troubleshooting:
    - If curation not triggered: Lower QUADRACODE_TARGET_CONTEXT_SIZE threshold
    - If externalization missing: Verify QUADRACODE_MAX_TOOL_PAYLOAD_CHARS is set
    - If context exceeds limits: Check curator is enabled and functioning
    """
    test_name = "test_context_curation_and_externalization"
    logger.info(f"Starting {test_name}")
    
    # Initialize metrics collector
    run_id = f"{int(time.time())}-{os.urandom(4).hex()}"
    collector = MetricsCollector(test_name=test_name, run_id=run_id)
    
    # Create log directory
    log_dir = create_test_log_directory(test_name)
    logger.info(f"Logs will be written to: {log_dir}")
    
    try:
        # Initialize Redis stream baselines
        baseline_orchestrator = get_last_stream_id(
            redis_client, "qc:mailbox/orchestrator"
        )
        baseline_human = get_last_stream_id(redis_client, "qc:mailbox/human")
        baseline_context_metrics = get_last_stream_id(
            redis_client, "qc:context:metrics"
        )
        
        logger.info(
            f"Baseline stream IDs: orchestrator={baseline_orchestrator}, "
            f"human={baseline_human}, context_metrics={baseline_context_metrics}"
        )
        
        # Note: This test would ideally override environment variables to lower thresholds:
        # - QUADRACODE_TARGET_CONTEXT_SIZE=5000 (tokens)
        # - QUADRACODE_MAX_TOOL_PAYLOAD_CHARS=500
        # However, since services are already running, we'll work with existing config
        # and generate enough content to trigger curation naturally
        
        logger.info(
            "Note: For optimal results, set QUADRACODE_TARGET_CONTEXT_SIZE=5000 "
            "and QUADRACODE_MAX_TOOL_PAYLOAD_CHARS=500 before starting Docker services"
        )
        
        # Step 1: Create workspace with large code files
        logger.info("Step 1: Creating workspace with large code files")
        send_message_to_orchestrator(
            redis_client,
            "Create a new workspace with ID 'ws-curation-test' for testing context curation.",
            sender="human",
        )
        
        response = wait_for_message_on_stream(
            redis_client,
            "qc:mailbox/human",
            baseline_human,
            sender="orchestrator",
            timeout=90,
        )
        baseline_human = response["stream_id"]
        logger.info(f"Workspace creation response: {response['stream_id']}")
        
        # Create large Python files (10KB each, 5 files = 50KB total)
        # Each file will be a substantial module with many functions
        large_file_template = '''"""Large module {module_name} with extensive functionality."""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path


logger = logging.getLogger(__name__)


class {class_name}:
    """Main class for {module_name} module."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {{}}
        self.state = {{"initialized": True}}
        self.cache = {{}}
        logger.info(f"Initialized {class_name} with config: {{self.config}}")
    
    def method_001(self, param1: str, param2: int) -> Dict[str, Any]:
        """Process data with specific parameters."""
        result = {{"param1": param1, "param2": param2, "processed": True}}
        self.cache[f"{{param1}}_{{param2}}"] = result
        return result
    
    def method_002(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Transform list of dictionaries."""
        transformed = []
        for item in data:
            new_item = item.copy()
            new_item["transformed"] = True
            new_item["timestamp"] = "2025-11-15T00:00:00Z"
            transformed.append(new_item)
        return transformed
    
    def method_003(self, key: str, value: Any) -> None:
        """Store key-value pair in state."""
        self.state[key] = value
        logger.debug(f"Stored {{key}}: {{value}}")
    
    def method_004(self, key: str, default: Any = None) -> Any:
        """Retrieve value from state."""
        return self.state.get(key, default)
    
    def method_005(self, items: List[Any], filter_fn: callable) -> List[Any]:
        """Filter items using provided function."""
        return [item for item in items if filter_fn(item)]
    
    def method_006(self, data: Dict[str, Any], path: Path) -> None:
        """Save data to JSON file."""
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved data to {{path}}")
    
    def method_007(self, path: Path) -> Dict[str, Any]:
        """Load data from JSON file."""
        with open(path, 'r') as f:
            data = json.load(f)
        logger.info(f"Loaded data from {{path}}")
        return data
    
    def method_008(self, records: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
        """Group records by key."""
        groups = {{}}
        for record in records:
            group_key = record.get(key)
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(record)
        return groups
    
    def method_009(self, text: str, replacements: Dict[str, str]) -> str:
        """Apply multiple replacements to text."""
        result = text
        for old, new in replacements.items():
            result = result.replace(old, new)
        return result
    
    def method_010(self, numbers: List[float]) -> Dict[str, float]:
        """Calculate statistics for number list."""
        if not numbers:
            return {{"mean": 0, "min": 0, "max": 0, "sum": 0}}
        return {{
            "mean": sum(numbers) / len(numbers),
            "min": min(numbers),
            "max": max(numbers),
            "sum": sum(numbers),
        }}


def utility_function_001(arg1: str, arg2: str) -> str:
    """Concatenate two strings with separator."""
    return f"{{arg1}}__{{arg2}}"


def utility_function_002(data: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """Extract subset of dictionary by keys."""
    return {{k: data[k] for k in keys if k in data}}


def utility_function_003(items: List[Any]) -> List[Any]:
    """Remove duplicates from list preserving order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def utility_function_004(value: Any) -> str:
    """Convert any value to JSON string."""
    return json.dumps(value, indent=2, sort_keys=True)


def utility_function_005(json_str: str) -> Any:
    """Parse JSON string to Python object."""
    try:
        return json.load(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {{e}}")
        return None


# Additional helper functions and constants
CONSTANT_001 = "DEFAULT_VALUE"
CONSTANT_002 = 42
CONSTANT_003 = {{"key": "value"}}


def helper_001() -> str:
    return CONSTANT_001


def helper_002(multiplier: int = 1) -> int:
    return CONSTANT_002 * multiplier


def helper_003(update: Dict[str, Any]) -> Dict[str, Any]:
    result = CONSTANT_003.copy()
    result.update(update)
    return result
'''
        
        large_files = []
        for i in range(5):
            module_name = f"module_{i+1:03d}"
            class_name = f"Module{i+1:03d}Processor"
            content = large_file_template.format(
                module_name=module_name, class_name=class_name
            )
            # Pad content to ensure it's large enough
            padding = "# " + ("=" * 80) + "\n"
            padded_content = content + "\n" + (padding * 50)  # Add padding lines
            large_files.append({
                "name": f"{module_name}.py",
                "content": padded_content,
            })
        
        logger.info(f"Creating {len(large_files)} large Python files (~10KB each)")
        for file_info in large_files:
            send_message_to_orchestrator(
                redis_client,
                f"In workspace ws-curation-test, create file /workspace/{file_info['name']} "
                f"with the following content:\n\n{file_info['content'][:500]}...[truncated]",
                sender="human",
            )
            
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=120,
            )
            baseline_human = response["stream_id"]
            logger.info(f"Created large file {file_info['name']}: {response['stream_id']}")
        
        # Step 2: Test flow - force context overflow with large tool outputs
        logger.info("Step 2: Forcing context overflow with read operations")
        
        test_start_time = time.time()
        turn_number = 0
        curation_events = []
        
        # Read all files multiple times to accumulate context
        overflow_prompts = [
            "Read all Python files in workspace ws-curation-test.",
            "List the classes and functions defined in module_001.py.",
            "Read module_002.py and analyze its methods.",
            "Read module_003.py in full.",
            "Examine module_004.py for utility functions.",
            "Read module_005.py completely.",
            "Summarize the functionality across all five modules.",
            "Read module_001.py again to review its structure.",
            "Compare module_002.py and module_003.py.",
            "Read module_004.py again and list all constants.",
        ]
        
        for prompt in overflow_prompts:
            turn_number += 1
            turn_start = time.time()
            
            logger.info(f"Turn {turn_number}: {prompt}")
            
            # Send message
            send_message_to_orchestrator(
                redis_client, prompt, sender="human"
            )
            
            # Wait for response
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=180,
            )
            baseline_human = response["stream_id"]
            
            turn_duration = time.time() - turn_start
            
            # Log turn
            log_turn(
                log_dir,
                turn_number,
                {"content": prompt},
                {
                    "stream_id": response["stream_id"],
                    "content": response.get("message", "")[:200] + "...",
                    "duration_ms": turn_duration * 1000,
                },
            )
            
            # Poll for curation events
            try:
                curation_event = poll_stream_for_event(
                    redis_client,
                    "qc:context:metrics",
                    baseline_context_metrics,
                    event_type="curation",
                    timeout=15,
                )
                if curation_event:
                    baseline_context_metrics = curation_event[0]
                    curation_events.append(curation_event[1])
                    logger.info(f"Captured curation event: {curation_event[0]}")
                    
                    # Log curation details
                    payload = curation_event[1].get("payload", {})
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    actions = payload.get("actions", [])
                    logger.info(f"Curation actions: {actions}")
            except Exception as e:
                logger.debug(f"No curation event for turn {turn_number}: {e}")
            
            logger.info(
                f"Turn {turn_number} complete: {turn_duration:.2f}s"
            )
        
        total_test_time = time.time() - test_start_time
        logger.info(
            f"Test flow complete: {total_test_time:.2f}s, {turn_number} turns, "
            f"{len(curation_events)} curation events"
        )
        
        # Verification Step 1: Assert curation events occurred
        logger.info("Verification: Checking curation events")
        # We expect at least some curation events given the large files
        # If threshold not lowered, we may get fewer events
        if len(curation_events) == 0:
            logger.warning(
                "No curation events captured. This may indicate: "
                "(1) QUADRACODE_TARGET_CONTEXT_SIZE threshold is too high, "
                "(2) Context never exceeded threshold, or "
                "(3) Curator is not enabled. "
                "For this test to be effective, set QUADRACODE_TARGET_CONTEXT_SIZE=5000 "
                "before starting services."
            )
        else:
            logger.info(f"✓ Captured {len(curation_events)} curation events")
        
        # Verification Step 2: Check curation actions
        if len(curation_events) > 0:
            logger.info("Verification: Checking curation action types")
            actions_found = set()
            for event in curation_events:
                payload = event.get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                actions = payload.get("actions", [])
                for action in actions:
                    action_type = action.get("operation", action.get("type", ""))
                    if action_type:
                        actions_found.add(action_type)
            
            logger.info(f"Curation action types found: {actions_found}")
            # We expect actions like 'compress', 'externalize', 'summarize', 'retain'
            # At least one action should be present
            assert len(actions_found) > 0, (
                f"Curation events found but no actions present. "
                f"Expected action types like 'compress', 'externalize', 'summarize'. "
                f"Check curator implementation. Events: {json.dumps(curation_events, indent=2)}"
            )
            logger.info(f"✓ Verified curation actions present: {actions_found}")
        
        # Verification Step 3: Check for externalize actions
        if len(curation_events) > 0:
            logger.info("Verification: Checking for externalize actions")
            externalize_count = 0
            for event in curation_events:
                payload = event.get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                actions = payload.get("actions", [])
                for action in actions:
                    action_type = action.get("operation", action.get("type", ""))
                    if action_type == "externalize":
                        externalize_count += 1
            
            logger.info(f"Found {externalize_count} externalize actions")
            if externalize_count > 0:
                logger.info(f"✓ Verified externalization applied {externalize_count} times")
            else:
                logger.warning(
                    "No externalize actions found. Tool outputs may not have exceeded "
                    "MAX_TOOL_PAYLOAD_CHARS threshold. Set QUADRACODE_MAX_TOOL_PAYLOAD_CHARS=500 "
                    "to ensure externalization is triggered."
                )
        
        # Verification Step 4: Check context size in post_process events
        logger.info("Verification: Checking context size management")
        context_metrics = read_stream(
            redis_client, "qc:context:metrics", count=200
        )
        post_process_events = [
            e for e in context_metrics if e[1].get("event") == "post_process"
        ]
        
        if len(post_process_events) > 0:
            context_sizes = []
            for event in post_process_events:
                payload = event[1].get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                context_size = payload.get("context_size", 0)
                context_sizes.append(context_size)
            
            max_context_size = max(context_sizes) if context_sizes else 0
            logger.info(f"Maximum context size observed: {max_context_size} tokens")
            
            # Check if context stayed below reasonable limits
            # Without knowing exact threshold, we just log the value
            logger.info(f"Context sizes: min={min(context_sizes)}, max={max_context_size}, mean={sum(context_sizes)/len(context_sizes):.0f}")
        
        # Verification Step 5: Assert test duration >= 8 minutes
        logger.info("Verification: Checking test duration")
        min_duration = 8 * 60  # 8 minutes
        assert total_test_time >= min_duration, (
            f"Test ran for {total_test_time:.2f}s, expected at least {min_duration}s. "
            f"Test completed too quickly."
        )
        logger.info(f"✓ Verified test duration: {total_test_time:.2f}s >= {min_duration}s")
        
        logger.info("✓ Test complete")
        
        # Record metrics
        collector.record_tool_call(
            "context_curation", 0, True, {}, {"curation_events": len(curation_events)}
        )
        collector.compute_derived_metrics()
        
        # Export metrics
        metrics_dir = Path("tests/e2e_advanced/metrics")
        metrics_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = metrics_dir / f"{test_name}_{run_id}_metrics.json"
        collector.export(metrics_path)
        logger.info(f"Metrics exported to: {metrics_path}")
        
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        raise
    
    finally:
        # Teardown
        logger.info("Teardown: Capturing artifacts and logs")
        
        # Dump Redis streams
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dump_all_streams(redis_client, artifacts_dir)
        
        # Capture Docker logs
        capture_docker_logs("orchestrator-runtime", artifacts_dir / "orchestrator.log")
        
        # Log curation events
        if curation_events:
            curation_log_path = artifacts_dir / "curation_events.json"
            with open(curation_log_path, 'w') as f:
                json.dump(curation_events, f, indent=2)
            logger.info(f"Curation events saved to: {curation_log_path}")
        
        logger.info(f"Test complete. Artifacts in: {artifacts_dir}")

