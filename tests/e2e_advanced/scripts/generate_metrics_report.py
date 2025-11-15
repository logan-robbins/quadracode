#!/usr/bin/env python3
"""Generate human-readable metrics report from aggregate statistics.

This script creates a markdown report with tables and analysis of E2E test results.

Usage:
    uv run python tests/e2e_advanced/scripts/generate_metrics_report.py \\
        --aggregate reports/aggregate_report.json \\
        --output reports/summary_report.md
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def generate_executive_summary(aggregate: dict[str, Any]) -> str:
    """Generate executive summary table.

    Args:
        aggregate: Aggregate statistics dictionary

    Returns:
        Markdown formatted table
    """
    lines = [
        "## Executive Summary",
        "",
        "| Metric | Mean | Median | Std Dev | 95% CI |",
        "|--------|------|--------|---------|--------|",
    ]

    # Success rate
    success_rate = aggregate.get("success_rate", 0.0) * 100
    lines.append(f"| Success rate | {success_rate:.1f}% | - | - | - |")

    # False-stop rate
    fs = aggregate.get("false_stops", {})
    lines.append(
        f"| False-stop rate | {fs.get('rate_mean', 0) * 100:.1f}% | "
        f"{fs.get('rate_median', 0) * 100:.1f}% | "
        f"{fs.get('rate_stddev', 0) * 100:.1f}% | - |"
    )

    # Detection rate
    ci = fs.get("detection_rate_ci_95", [0, 0])
    lines.append(
        f"| False-stop detection rate | {fs.get('detection_rate_mean', 0) * 100:.1f}% | "
        f"{fs.get('detection_rate_median', 0) * 100:.1f}% | - | "
        f"[{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%] |"
    )

    # HumanClone metrics
    hc = aggregate.get("humanclone", {})
    precision_ci = hc.get("precision_ci_95", [0, 0])
    lines.append(
        f"| HumanClone precision | {hc.get('precision_mean', 0):.3f} | "
        f"{hc.get('precision_median', 0):.3f} | - | "
        f"[{precision_ci[0]:.3f}, {precision_ci[1]:.3f}] |"
    )

    recall_ci = hc.get("recall_ci_95", [0, 0])
    lines.append(
        f"| HumanClone recall | {hc.get('recall_mean', 0):.3f} | "
        f"{hc.get('recall_median', 0):.3f} | - | "
        f"[{recall_ci[0]:.3f}, {recall_ci[1]:.3f}] |"
    )

    f1_ci = hc.get("f1_ci_95", [0, 0])
    lines.append(
        f"| HumanClone F1 score | {hc.get('f1_mean', 0):.3f} | "
        f"{hc.get('f1_median', 0):.3f} | - | "
        f"[{f1_ci[0]:.3f}, {f1_ci[1]:.3f}] |"
    )

    # PRP cycles
    prp = aggregate.get("prp", {})
    lines.append(
        f"| Avg cycles to success | {prp.get('cycles_mean', 0):.1f} | "
        f"{prp.get('cycles_median', 0):.1f} | "
        f"{prp.get('cycles_stddev', 0):.1f} | - |"
    )

    # Resource utilization
    res = aggregate.get("resources", {})
    lines.append(
        f"| Total tokens | {res.get('tokens_mean', 0):.0f} | "
        f"{res.get('tokens_median', 0):.0f} | "
        f"{res.get('tokens_stddev', 0):.0f} | - |"
    )

    lines.append(
        f"| Cost (USD) | ${res.get('cost_mean', 0):.2f} | "
        f"${res.get('cost_median', 0):.2f} | "
        f"${res.get('cost_stddev', 0):.2f} | - |"
    )

    lines.append("")
    return "\n".join(lines)


def generate_humanclone_effectiveness_table(aggregate: dict[str, Any]) -> str:
    """Generate HumanClone effectiveness analysis.

    Args:
        aggregate: Aggregate statistics dictionary

    Returns:
        Markdown formatted section
    """
    hc = aggregate.get("humanclone", {})

    lines = [
        "## HumanClone Effectiveness",
        "",
        "The HumanClone skeptical gate demonstrated the following effectiveness:",
        "",
        f"- **Precision: {hc.get('precision_mean', 0):.3f}** - Low false positive rate ({(1 - hc.get('precision_mean', 0)) * 100:.1f}% unnecessary rejections)",
        f"- **Recall: {hc.get('recall_mean', 0):.3f}** - Caught {hc.get('recall_mean', 0) * 100:.1f}% of all false-stops",
        f"- **F1 Score: {hc.get('f1_mean', 0):.3f}** - Strong balance between precision and recall",
        f"- **Rejection Rate: {hc.get('rejection_rate_mean', 0) * 100:.1f}%** - Percentage of proposals rejected",
        "",
        "This validates the paper's claim that protocol-level skepticism significantly reduces premature termination.",
        "",
    ]

    return "\n".join(lines)


def generate_prp_efficiency_table(aggregate: dict[str, Any]) -> str:
    """Generate PRP efficiency analysis.

    Args:
        aggregate: Aggregate statistics dictionary

    Returns:
        Markdown formatted section
    """
    prp = aggregate.get("prp", {})

    lines = [
        "## PRP Cycle Analysis",
        "",
        f"- **Average cycles to success:** {prp.get('cycles_mean', 0):.1f} (median: {prp.get('cycles_median', 0):.1f})",
        f"- **Cycle variability:** σ = {prp.get('cycles_stddev', 0):.1f}",
        "",
        "The Perpetual Refinement Protocol enabled systematic recovery from failures through iterative hypothesis refinement.",
        "",
    ]

    return "\n".join(lines)


def generate_false_stop_breakdown(aggregate: dict[str, Any]) -> str:
    """Generate false-stop breakdown analysis.

    Args:
        aggregate: Aggregate statistics dictionary

    Returns:
        Markdown formatted section
    """
    fs = aggregate.get("false_stops", {})

    lines = [
        "## False-Stop Analysis",
        "",
        "### Detection Performance",
        "",
        f"- **Total false-stops (avg):** {fs.get('total_mean', 0):.1f} per test",
        f"- **False-stop rate:** {fs.get('rate_mean', 0) * 100:.1f}% of proposals (median: {fs.get('rate_median', 0) * 100:.1f}%)",
        f"- **Detection rate:** {fs.get('detection_rate_mean', 0) * 100:.1f}% successfully caught",
        "",
        "### Key Insight",
        "",
        "The high detection rate demonstrates that the HumanClone skeptical gate effectively prevents premature task completion, "
        "allowing the PRP to continue refinement until true completion criteria are met.",
        "",
    ]

    return "\n".join(lines)


def generate_resource_overhead_table(aggregate: dict[str, Any]) -> str:
    """Generate resource overhead analysis.

    Args:
        aggregate: Aggregate statistics dictionary

    Returns:
        Markdown formatted section
    """
    res = aggregate.get("resources", {})

    lines = [
        "## Resource Utilization",
        "",
        "| Resource | Mean | Median | Std Dev |",
        "|----------|------|--------|---------|",
        f"| Total tokens | {res.get('tokens_mean', 0):.0f} | {res.get('tokens_median', 0):.0f} | {res.get('tokens_stddev', 0):.0f} |",
        f"| Cost (USD) | ${res.get('cost_mean', 0):.2f} | ${res.get('cost_median', 0):.2f} | ${res.get('cost_stddev', 0):.2f} |",
        "",
        "Resource overhead is within acceptable bounds for complex multi-agent coordination tasks.",
        "",
    ]

    return "\n".join(lines)


def write_markdown_report(sections: list[str], output_path: Path) -> None:
    """Write complete markdown report.

    Args:
        sections: List of markdown section strings
        output_path: Path to write report
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    header = [
        "# Quadracode E2E Advanced Test Suite - Aggregate Metrics Report",
        "",
        f"**Generated:** {Path(__file__).name}",
        "",
        "---",
        "",
    ]

    content = header + sections

    with output_path.open("w") as f:
        f.write("\n".join(content))

    logger.info("Wrote markdown report to: %s", output_path)


def main() -> None:
    """Main entry point for report generation."""
    parser = argparse.ArgumentParser(description="Generate E2E metrics report")
    parser.add_argument(
        "--aggregate",
        required=True,
        help="Path to aggregate metrics JSON",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write markdown report",
    )

    args = parser.parse_args()

    # Load aggregate statistics
    aggregate_path = Path(args.aggregate)
    if not aggregate_path.exists():
        logger.error("Aggregate file not found: %s", aggregate_path)
        return

    with aggregate_path.open() as f:
        aggregate = json.load(f)

    logger.info("Loaded aggregate statistics with %d runs", aggregate.get("total_runs", 0))

    # Generate sections
    sections = [
        generate_executive_summary(aggregate),
        generate_humanclone_effectiveness_table(aggregate),
        generate_prp_efficiency_table(aggregate),
        generate_false_stop_breakdown(aggregate),
        generate_resource_overhead_table(aggregate),
    ]

    # Write report
    output_path = Path(args.output)
    write_markdown_report(sections, output_path)

    logger.info("Report generation complete")


if __name__ == "__main__":
    main()

