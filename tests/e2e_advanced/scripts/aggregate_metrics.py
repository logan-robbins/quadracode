#!/usr/bin/env python3
"""Aggregate metrics from multiple E2E test runs.

This script loads individual test metrics files, validates them against the schema,
and computes aggregate statistics with confidence intervals.

Usage:
    uv run python tests/e2e_advanced/scripts/aggregate_metrics.py \\
        --input "metrics/*.json" \\
        --output reports/aggregate_report.json
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import statistics
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def load_metrics_files(pattern: str) -> list[dict[str, Any]]:
    """Load all metrics files matching the glob pattern.

    Args:
        pattern: Glob pattern for metrics files (e.g., "metrics/*.json")

    Returns:
        List of metrics dictionaries

    Raises:
        FileNotFoundError: If no files match pattern
    """
    # Expand glob pattern
    file_paths = glob.glob(pattern)
    if not file_paths:
        raise FileNotFoundError(f"No files match pattern: {pattern}")

    logger.info("Found %d metrics files", len(file_paths))

    metrics_list = []
    for file_path in file_paths:
        try:
            with open(file_path) as f:
                metrics = json.load(f)
                metrics_list.append(metrics)
                logger.debug("Loaded: %s", file_path)
        except Exception as e:
            logger.error("Failed to load %s: %s", file_path, e)

    logger.info("Successfully loaded %d metrics files", len(metrics_list))
    return metrics_list


def validate_metrics_against_schema(metrics: dict[str, Any], schema_path: Path) -> list[str]:
    """Validate metrics against JSON schema.

    Args:
        metrics: Metrics dictionary
        schema_path: Path to schema JSON file

    Returns:
        List of validation error messages (empty if valid)
    """
    try:
        import jsonschema
    except ImportError:
        logger.warning("jsonschema not installed, skipping validation")
        return []

    try:
        with schema_path.open() as f:
            schema = json.load(f)

        jsonschema.validate(instance=metrics, schema=schema)
        return []
    except jsonschema.ValidationError as e:
        return [str(e)]
    except Exception as e:
        return [f"Schema validation error: {e}"]


def compute_aggregate_statistics(metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate statistics across all test runs.

    Args:
        metrics_list: List of metrics dictionaries

    Returns:
        Aggregate statistics dictionary
    """
    if not metrics_list:
        return {}

    aggregate = {
        "total_runs": len(metrics_list),
        "successful_runs": sum(1 for m in metrics_list if m.get("success")),
        "failed_runs": sum(1 for m in metrics_list if not m.get("success")),
        "success_rate": 0.0,
        "false_stops": {},
        "humanclone": {},
        "prp": {},
        "resources": {},
    }

    # Success rate
    aggregate["success_rate"] = aggregate["successful_runs"] / aggregate["total_runs"]

    # False-stop metrics
    false_stop_totals = [m["false_stops"]["total"] for m in metrics_list]
    false_stop_rates = [m["false_stops"]["rate"] for m in metrics_list]
    detection_rates = [m["false_stops"]["detection_rate"] for m in metrics_list]

    aggregate["false_stops"] = {
        "total_mean": statistics.mean(false_stop_totals) if false_stop_totals else 0.0,
        "total_median": statistics.median(false_stop_totals) if false_stop_totals else 0.0,
        "total_stddev": statistics.stdev(false_stop_totals) if len(false_stop_totals) > 1 else 0.0,
        "rate_mean": statistics.mean(false_stop_rates) if false_stop_rates else 0.0,
        "rate_median": statistics.median(false_stop_rates) if false_stop_rates else 0.0,
        "rate_stddev": statistics.stdev(false_stop_rates) if len(false_stop_rates) > 1 else 0.0,
        "detection_rate_mean": statistics.mean(detection_rates) if detection_rates else 0.0,
        "detection_rate_median": statistics.median(detection_rates) if detection_rates else 0.0,
        "detection_rate_ci_95": _bootstrap_ci(detection_rates) if detection_rates else [0.0, 0.0],
    }

    # HumanClone metrics
    precisions = [m["humanclone"]["precision"] for m in metrics_list]
    recalls = [m["humanclone"]["recall"] for m in metrics_list]
    f1_scores = [m["humanclone"]["f1_score"] for m in metrics_list]
    rejection_rates = [m["humanclone"]["rejection_rate"] for m in metrics_list]

    aggregate["humanclone"] = {
        "precision_mean": statistics.mean(precisions) if precisions else 0.0,
        "precision_median": statistics.median(precisions) if precisions else 0.0,
        "precision_ci_95": _bootstrap_ci(precisions) if precisions else [0.0, 0.0],
        "recall_mean": statistics.mean(recalls) if recalls else 0.0,
        "recall_median": statistics.median(recalls) if recalls else 0.0,
        "recall_ci_95": _bootstrap_ci(recalls) if recalls else [0.0, 0.0],
        "f1_mean": statistics.mean(f1_scores) if f1_scores else 0.0,
        "f1_median": statistics.median(f1_scores) if f1_scores else 0.0,
        "f1_ci_95": _bootstrap_ci(f1_scores) if f1_scores else [0.0, 0.0],
        "rejection_rate_mean": statistics.mean(rejection_rates) if rejection_rates else 0.0,
    }

    # PRP metrics
    cycle_counts = [m["prp"]["total_cycles"] for m in metrics_list]

    aggregate["prp"] = {
        "cycles_mean": statistics.mean(cycle_counts) if cycle_counts else 0.0,
        "cycles_median": statistics.median(cycle_counts) if cycle_counts else 0.0,
        "cycles_stddev": statistics.stdev(cycle_counts) if len(cycle_counts) > 1 else 0.0,
    }

    # Resource metrics
    token_counts = [m["resources"]["total_tokens"] for m in metrics_list]
    costs = [m["resources"]["total_cost_usd"] for m in metrics_list]

    aggregate["resources"] = {
        "tokens_mean": statistics.mean(token_counts) if token_counts else 0.0,
        "tokens_median": statistics.median(token_counts) if token_counts else 0.0,
        "tokens_stddev": statistics.stdev(token_counts) if len(token_counts) > 1 else 0.0,
        "cost_mean": statistics.mean(costs) if costs else 0.0,
        "cost_median": statistics.median(costs) if costs else 0.0,
        "cost_stddev": statistics.stdev(costs) if len(costs) > 1 else 0.0,
    }

    return aggregate


def _bootstrap_ci(values: list[float], confidence: float = 0.95, n_bootstrap: int = 1000) -> list[float]:
    """Compute bootstrap confidence interval.

    Args:
        values: List of numeric values
        confidence: Confidence level (default: 0.95 for 95% CI)
        n_bootstrap: Number of bootstrap samples

    Returns:
        [lower_bound, upper_bound] list
    """
    if not values or len(values) < 2:
        return [0.0, 0.0]

    import random

    bootstrap_means = []
    for _ in range(n_bootstrap):
        sample = random.choices(values, k=len(values))
        bootstrap_means.append(statistics.mean(sample))

    bootstrap_means.sort()
    lower_idx = int((1 - confidence) / 2 * n_bootstrap)
    upper_idx = int((1 + confidence) / 2 * n_bootstrap)

    return [bootstrap_means[lower_idx], bootstrap_means[upper_idx]]


def export_aggregate_json(stats: dict[str, Any], output_path: Path) -> None:
    """Export aggregate statistics to JSON.

    Args:
        stats: Aggregate statistics dictionary
        output_path: Path to write JSON file
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(stats, f, indent=2)
    logger.info("Exported aggregate JSON to: %s", output_path)


def export_aggregate_csv(stats: dict[str, Any], output_path: Path) -> None:
    """Export aggregate statistics to CSV.

    Args:
        stats: Aggregate statistics dictionary
        output_path: Path to write CSV file
    """
    import csv

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Flatten nested dict to rows
    rows = []

    def flatten(d: dict, prefix: str = ""):
        for key, value in d.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                flatten(value, full_key)
            elif isinstance(value, list):
                rows.append({"metric": full_key, "value": str(value)})
            else:
                rows.append({"metric": full_key, "value": value})

    flatten(stats)

    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["metric", "value"])
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Exported aggregate CSV to: %s", output_path)


def main() -> None:
    """Main entry point for metrics aggregation."""
    parser = argparse.ArgumentParser(description="Aggregate E2E test metrics")
    parser.add_argument(
        "--input",
        required=True,
        help='Glob pattern for metrics files (e.g., "metrics/*.json")',
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write aggregate JSON report",
    )
    parser.add_argument(
        "--csv",
        help="Optional path to write CSV export",
    )
    parser.add_argument(
        "--schema",
        help="Path to metrics schema for validation",
        default="tests/e2e_advanced/schemas/metrics_schema.json",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate metrics against schema before aggregating",
    )

    args = parser.parse_args()

    # Load metrics files
    metrics_list = load_metrics_files(args.input)

    if not metrics_list:
        logger.error("No metrics files loaded, exiting")
        return

    # Optional validation
    if args.validate:
        schema_path = Path(args.schema)
        if schema_path.exists():
            logger.info("Validating metrics against schema...")
            for i, metrics in enumerate(metrics_list):
                errors = validate_metrics_against_schema(metrics, schema_path)
                if errors:
                    logger.warning("Validation errors in file %d: %s", i, errors)
        else:
            logger.warning("Schema file not found: %s", schema_path)

    # Compute aggregate statistics
    logger.info("Computing aggregate statistics...")
    aggregate = compute_aggregate_statistics(metrics_list)

    # Export results
    output_path = Path(args.output)
    export_aggregate_json(aggregate, output_path)

    if args.csv:
        csv_path = Path(args.csv)
        export_aggregate_csv(aggregate, csv_path)

    logger.info("Aggregation complete")
    logger.info("Total runs: %d", aggregate["total_runs"])
    logger.info("Success rate: %.1f%%", aggregate["success_rate"] * 100)
    logger.info("False-stop detection rate (mean): %.1f%%", aggregate["false_stops"]["detection_rate_mean"] * 100)
    logger.info("HumanClone precision (mean): %.3f", aggregate["humanclone"]["precision_mean"])
    logger.info("HumanClone recall (mean): %.3f", aggregate["humanclone"]["recall_mean"])


if __name__ == "__main__":
    main()

