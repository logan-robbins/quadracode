"""Metrics collection system for advanced E2E tests.

This module provides the MetricsCollector class for tracking false-stops,
HumanClone effectiveness, PRP cycles, and resource utilization.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collects, validates, and exports metrics for Quadracode E2E tests.

    Usage:
        collector = MetricsCollector(test_name="test_prp_autonomous", run_id="abc123")
        collector.record_false_stop(proposal_message, detected_by="humanclone")
        collector.record_humanclone_invocation(proposal, trigger, outcome="rejection")
        collector.record_prp_transition(from_state="TEST", to_state="CONCLUDE", valid=True)
        collector.export(output_path)
    """

    def __init__(self, test_name: str, run_id: str):
        """Initialize metrics collector.

        Args:
            test_name: Name of the test
            run_id: Unique identifier for this test run
        """
        self.test_name = test_name
        self.run_id = run_id
        self.start_time = time.time()
        self.metrics = self._initialize_metrics()
        self.events: list[dict[str, Any]] = []  # Timestamped event log

    def _initialize_metrics(self) -> dict[str, Any]:
        """Create default metrics structure.

        Returns:
            Empty metrics dict with all required sections
        """
        return {
            "test_name": self.test_name,
            "run_id": self.run_id,
            "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.start_time)),
            "end_time": None,
            "duration_ms": 0,
            "success": False,
            "false_stops": {
                "total": 0,
                "rate": 0.0,
                "detected_by_humanclone": 0,
                "detection_rate": 0.0,
                "uncaught_false_stops": 0,
                "by_stage": {},
                "instances": [],
            },
            "humanclone": {
                "total_invocations": 0,
                "rejections": 0,
                "acceptances": 0,
                "rejection_rate": 0.0,
                "correct_rejections": 0,
                "incorrect_rejections": 0,
                "precision": 0.0,
                "recall": 0.0,
                "f1_score": 0.0,
                "avg_latency_ms": 0.0,
                "latency_p50_ms": 0.0,
                "latency_p95_ms": 0.0,
                "trigger_exhaustion_modes": {},
                "trigger_details": [],
            },
            "prp": {
                "total_cycles": 0,
                "cycles_to_success": None,
                "cycles_to_failure": None,
                "state_distribution": {},
                "transition_counts": {},
                "invalid_transitions": 0,
                "exhaustion_triggers": 0,
                "novelty_scores": [],
                "improvement_detected_per_cycle": [],
                "stall_cycles": 0,
                "cycles": [],
            },
            "resources": {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_cost_usd": 0.0,
                "messages_sent": 0,
                "messages_by_recipient": {},
                "tool_calls_total": 0,
                "tool_calls_success": 0,
                "tool_calls_failure": 0,
                "tool_call_success_rate": 0.0,
                "tool_call_avg_latency_ms": 0.0,
                "tool_calls_by_type": {},
                "context_overflow_events": 0,
                "context_curation_events": 0,
            },
            "completion": {
                "test_success": False,
                "task_verification_passed": False,
                "final_state": None,
                "escalation_triggered": False,
                "escalation_reason": None,
            },
        }

    def record_false_stop(
        self,
        proposal: dict[str, Any],
        detected_by: str,
        stage: str,
        recovery_time_ms: int | None = None,
    ) -> None:
        """Record a false-stop event.

        Args:
            proposal: Orchestrator proposal message dict
            detected_by: Who detected it ("humanclone", "verification_script", etc.)
            stage: Stage classification (e.g., "incomplete_implementation")
            recovery_time_ms: Time from detection to next valid progress
        """
        instance = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "proposal_stream_id": proposal.get("stream_id"),
            "stage": stage,
            "detected_by": detected_by,
            "recovery_time_ms": recovery_time_ms,
            "llm_judge_classification": None,  # Filled in later by LLM judge
        }

        self.metrics["false_stops"]["instances"].append(instance)
        self.metrics["false_stops"]["total"] += 1

        if detected_by == "humanclone":
            self.metrics["false_stops"]["detected_by_humanclone"] += 1

        # Update by_stage counts
        by_stage = self.metrics["false_stops"]["by_stage"]
        by_stage[stage] = by_stage.get(stage, 0) + 1

        logger.debug("Recorded false-stop: stage=%s detected_by=%s", stage, detected_by)

    def record_orchestrator_proposal(self, proposal: dict[str, Any]) -> None:
        """Track a proposal sent to HumanClone.

        Args:
            proposal: Orchestrator proposal message dict
        """
        self.events.append({
            "event_type": "orchestrator_proposal",
            "timestamp": time.time(),
            "proposal": proposal,
        })

    def record_humanclone_invocation(
        self,
        proposal: dict[str, Any],
        trigger: dict[str, Any] | None,
        outcome: str,
        latency_ms: int | None = None,
        exhaustion_mode: str | None = None,
    ) -> None:
        """Record a HumanClone review interaction.

        Args:
            proposal: Orchestrator proposal message dict
            trigger: HumanCloneTrigger payload dict (if rejection)
            outcome: "rejection" or "acceptance"
            latency_ms: Response time from proposal to trigger
            exhaustion_mode: Exhaustion mode if rejection (e.g., "TEST_FAILURE")
        """
        self.metrics["humanclone"]["total_invocations"] += 1

        if outcome == "rejection":
            self.metrics["humanclone"]["rejections"] += 1
        elif outcome == "acceptance":
            self.metrics["humanclone"]["acceptances"] += 1

        invocation_detail = {
            "invocation_id": self.metrics["humanclone"]["total_invocations"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "outcome": outcome,
            "exhaustion_mode": exhaustion_mode or "NONE",
            "rationale": trigger.get("rationale") if trigger else None,
            "required_artifacts": trigger.get("required_artifacts", []) if trigger else [],
            "latency_ms": latency_ms,
        }

        self.metrics["humanclone"]["trigger_details"].append(invocation_detail)

        # Track exhaustion modes
        if exhaustion_mode:
            modes = self.metrics["humanclone"]["trigger_exhaustion_modes"]
            modes[exhaustion_mode] = modes.get(exhaustion_mode, 0) + 1

        logger.debug("Recorded HumanClone invocation: outcome=%s mode=%s", outcome, exhaustion_mode)

    def record_prp_transition(
        self,
        from_state: str,
        to_state: str,
        valid: bool,
        exhaustion_mode: str | None = None,
    ) -> None:
        """Track a PRP state machine transition.

        Args:
            from_state: Source state (e.g., "TEST")
            to_state: Destination state (e.g., "CONCLUDE")
            valid: Whether transition was allowed by guards
            exhaustion_mode: Exhaustion mode if relevant
        """
        transition_key = f"{from_state}->{to_state}"
        transitions = self.metrics["prp"]["transition_counts"]
        transitions[transition_key] = transitions.get(transition_key, 0) + 1

        if not valid:
            self.metrics["prp"]["invalid_transitions"] += 1

        if exhaustion_mode:
            self.metrics["prp"]["exhaustion_triggers"] += 1

        logger.debug("Recorded PRP transition: %s (valid=%s)", transition_key, valid)

    def record_prp_cycle(self, cycle_data: dict[str, Any]) -> None:
        """Record a complete PRP cycle.

        Args:
            cycle_data: Dict with keys: cycle_id, hypothesis, outcome, test_results,
                        novelty_score, duration_ms, improvement_detected
        """
        self.metrics["prp"]["cycles"].append(cycle_data)
        self.metrics["prp"]["total_cycles"] += 1

        if cycle_data.get("novelty_score") is not None:
            self.metrics["prp"]["novelty_scores"].append(cycle_data["novelty_score"])

        if "improvement_detected" in cycle_data:
            self.metrics["prp"]["improvement_detected_per_cycle"].append(
                cycle_data["improvement_detected"]
            )

        logger.debug("Recorded PRP cycle: id=%s outcome=%s", cycle_data.get("cycle_id"), cycle_data.get("outcome"))

    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
        success: bool,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        """Record a tool execution.

        Args:
            tool_name: Name of the tool (e.g., "workspace_exec")
            duration_ms: Execution time in milliseconds
            success: Whether tool call succeeded
            inputs: Optional tool input parameters
            outputs: Optional tool outputs
        """
        self.metrics["resources"]["tool_calls_total"] += 1

        if success:
            self.metrics["resources"]["tool_calls_success"] += 1
        else:
            self.metrics["resources"]["tool_calls_failure"] += 1

        # Update by-type counts
        by_type = self.metrics["resources"]["tool_calls_by_type"]
        by_type[tool_name] = by_type.get(tool_name, 0) + 1

        logger.debug("Recorded tool call: %s (success=%s, duration=%.1fms)", tool_name, success, duration_ms)

    def record_message(self, sender: str, recipient: str) -> None:
        """Record a message sent between services.

        Args:
            sender: Message sender ID
            recipient: Message recipient ID
        """
        self.metrics["resources"]["messages_sent"] += 1

        by_recipient = self.metrics["resources"]["messages_by_recipient"]
        by_recipient[recipient] = by_recipient.get(recipient, 0) + 1

    def record_verification_result(self, passed: bool, output: str, exit_code: int) -> None:
        """Store verification script results.

        Args:
            passed: Whether verification passed
            output: Verification script output
            exit_code: Script exit code
        """
        self.metrics["completion"]["task_verification_passed"] = passed
        self.events.append({
            "event_type": "verification",
            "timestamp": time.time(),
            "passed": passed,
            "output": output,
            "exit_code": exit_code,
        })

    def compute_derived_metrics(self) -> None:
        """Calculate rates, percentages, and aggregates after test completion."""
        end_time = time.time()
        self.metrics["end_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end_time))
        self.metrics["duration_ms"] = int((end_time - self.start_time) * 1000)

        # False-stop rates
        total_proposals = self.metrics["humanclone"]["total_invocations"]
        total_false_stops = self.metrics["false_stops"]["total"]
        detected_by_hc = self.metrics["false_stops"]["detected_by_humanclone"]

        if total_proposals > 0:
            self.metrics["false_stops"]["rate"] = self._calculate_rate(
                total_false_stops, total_proposals
            )

        if total_false_stops > 0:
            self.metrics["false_stops"]["detection_rate"] = self._calculate_rate(
                detected_by_hc, total_false_stops
            )

        # HumanClone metrics
        hc = self.metrics["humanclone"]
        if hc["total_invocations"] > 0:
            hc["rejection_rate"] = self._calculate_rate(hc["rejections"], hc["total_invocations"])

        if hc["rejections"] > 0:
            hc["precision"] = self._calculate_rate(hc["correct_rejections"], hc["rejections"])

        if total_false_stops > 0:
            hc["recall"] = self._calculate_rate(hc["correct_rejections"], total_false_stops)

        hc["f1_score"] = self._compute_f1_score(hc["precision"], hc["recall"])

        # HumanClone latencies
        latencies = [detail.get("latency_ms") for detail in hc["trigger_details"]
                     if detail.get("latency_ms") is not None]
        if latencies:
            hc["avg_latency_ms"] = sum(latencies) / len(latencies)
            sorted_latencies = sorted(latencies)
            hc["latency_p50_ms"] = sorted_latencies[len(sorted_latencies) // 2]
            hc["latency_p95_ms"] = sorted_latencies[int(len(sorted_latencies) * 0.95)]

        # Tool call metrics
        resources = self.metrics["resources"]
        if resources["tool_calls_total"] > 0:
            resources["tool_call_success_rate"] = self._calculate_rate(
                resources["tool_calls_success"], resources["tool_calls_total"]
            )

        logger.info("Computed derived metrics for test: %s", self.test_name)

    def validate_consistency(self) -> list[str]:
        """Run consistency checks on metrics.

        Returns:
            List of validation error messages (empty if all valid)
        """
        errors = []

        # Check false-stop consistency
        fs = self.metrics["false_stops"]
        if fs["detected_by_humanclone"] > fs["total"]:
            errors.append(
                f"detected_by_humanclone ({fs['detected_by_humanclone']}) > total ({fs['total']})"
            )

        # Check HumanClone consistency
        hc = self.metrics["humanclone"]
        if hc["rejections"] + hc["acceptances"] != hc["total_invocations"]:
            errors.append(
                f"rejections ({hc['rejections']}) + acceptances ({hc['acceptances']}) != "
                f"total_invocations ({hc['total_invocations']})"
            )

        if hc["correct_rejections"] > hc["rejections"]:
            errors.append(
                f"correct_rejections ({hc['correct_rejections']}) > rejections ({hc['rejections']})"
            )

        # Check tool call consistency
        resources = self.metrics["resources"]
        sum_by_type = sum(resources["tool_calls_by_type"].values())
        if sum_by_type != resources["tool_calls_total"]:
            errors.append(
                f"sum(tool_calls_by_type) ({sum_by_type}) != tool_calls_total ({resources['tool_calls_total']})"
            )

        return errors

    def export(self, output_path: Path) -> None:
        """Write metrics to JSON file with schema validation.

        Args:
            output_path: Path to write metrics JSON
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Validate consistency
        errors = self.validate_consistency()
        if errors:
            logger.warning("Metrics consistency errors: %s", errors)

        with output_path.open("w") as f:
            json.dump(self.metrics, f, indent=2, default=str)

        logger.info("Exported metrics to: %s", output_path)

    def get_proposals(self) -> list[dict[str, Any]]:
        """Get all orchestrator proposals from event log.

        Returns:
            List of proposal dicts
        """
        return [
            event["proposal"]
            for event in self.events
            if event.get("event_type") == "orchestrator_proposal"
        ]

    def add_judge_classification(self, proposal_id: str, classification: dict[str, Any]) -> None:
        """Add LLM-as-a-judge classification to a false-stop instance.

        Args:
            proposal_id: Stream ID of the proposal
            classification: Judge classification dict
        """
        for instance in self.metrics["false_stops"]["instances"]:
            if instance.get("proposal_stream_id") == proposal_id:
                instance["llm_judge_classification"] = classification
                break

    @staticmethod
    def _calculate_rate(numerator: int, denominator: int) -> float:
        """Safe division for rate calculation.

        Args:
            numerator: Top value
            denominator: Bottom value

        Returns:
            Rate (0.0-1.0), or 0.0 if denominator is zero
        """
        if denominator == 0:
            return 0.0
        return numerator / denominator

    @staticmethod
    def _compute_f1_score(precision: float, recall: float) -> float:
        """Compute harmonic mean of precision and recall.

        Args:
            precision: Precision value (0.0-1.0)
            recall: Recall value (0.0-1.0)

        Returns:
            F1 score (0.0-1.0)
        """
        if precision + recall == 0:
            return 0.0
        return 2 * (precision * recall) / (precision + recall)

