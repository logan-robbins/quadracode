#!/usr/bin/env python3
"""Generate visualizations from aggregate metrics.

This script creates plots for false-stop rates, HumanClone ROC curves,
PRP cycle distributions, and resource utilization.

Usage:
    uv run python tests/e2e_advanced/scripts/plot_metrics.py \\
        --aggregate reports/aggregate_report.json \\
        --output plots/
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def plot_false_stop_rate_by_test(aggregate: dict[str, Any], output_path: Path) -> None:
    """Generate bar chart of false-stop rates per test.

    Args:
        aggregate: Aggregate statistics dictionary
        output_path: Path to save plot
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.error("matplotlib not installed, cannot generate plots")
        return

    fs = aggregate.get("false_stops", {})

    fig, ax = plt.subplots(figsize=(10, 6))

    # Plot bar with error bars
    ax.bar(
        ["False-Stop Rate"],
        [fs.get("rate_mean", 0) * 100],
        yerr=[fs.get("rate_stddev", 0) * 100],
        capsize=5,
        color="steelblue",
        alpha=0.7,
    )

    ax.set_ylabel("False-Stop Rate (%)")
    ax.set_title("False-Stop Rate Across Test Runs")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    logger.info("Saved false-stop rate plot to: %s", output_path)


def plot_humanclone_roc_curve(aggregate: dict[str, Any], output_path: Path) -> None:
    """Generate ROC curve for HumanClone effectiveness.

    Args:
        aggregate: Aggregate statistics dictionary
        output_path: Path to save plot

    Note: This is a simplified representation showing precision/recall point
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    hc = aggregate.get("humanclone", {})
    precision = hc.get("precision_mean", 0)
    recall = hc.get("recall_mean", 0)

    fig, ax = plt.subplots(figsize=(8, 8))

    # Plot operating point
    ax.plot(
        [1 - precision],  # False positive rate
        [recall],  # True positive rate
        "ro",
        markersize=10,
        label=f"HumanClone (Precision={precision:.3f}, Recall={recall:.3f})",
    )

    # Plot diagonal (random classifier)
    ax.plot([0, 1], [0, 1], "k--", alpha=0.3, label="Random")

    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate (Recall)")
    ax.set_title("HumanClone Detection Performance")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1])

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    logger.info("Saved HumanClone ROC plot to: %s", output_path)


def plot_prp_cycle_distribution(aggregate: dict[str, Any], output_path: Path) -> None:
    """Generate histogram of PRP cycles to success.

    Args:
        aggregate: Aggregate statistics dictionary
        output_path: Path to save plot
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    prp = aggregate.get("prp", {})
    mean_cycles = prp.get("cycles_mean", 0)
    stddev_cycles = prp.get("cycles_stddev", 0)

    fig, ax = plt.subplots(figsize=(10, 6))

    # Create histogram representation (simplified since we only have aggregate stats)
    import numpy as np

    # Generate approximate distribution
    if stddev_cycles > 0:
        cycles = np.random.normal(mean_cycles, stddev_cycles, 1000)
        cycles = cycles[cycles > 0]  # Remove negative values
    else:
        cycles = [mean_cycles] * 100

    ax.hist(cycles, bins=20, color="steelblue", alpha=0.7, edgecolor="black")
    ax.axvline(mean_cycles, color="red", linestyle="--", linewidth=2, label=f"Mean: {mean_cycles:.1f}")

    ax.set_xlabel("Number of PRP Cycles")
    ax.set_ylabel("Frequency")
    ax.set_title("Distribution of PRP Cycles to Success")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    logger.info("Saved PRP cycle distribution plot to: %s", output_path)


def plot_recovery_time_cdf(aggregate: dict[str, Any], output_path: Path) -> None:
    """Generate cumulative distribution of recovery times.

    Args:
        aggregate: Aggregate statistics dictionary
        output_path: Path to save plot

    Note: This is a placeholder as recovery times need to be collected from individual runs
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    # Placeholder implementation
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.text(
        0.5,
        0.5,
        "Recovery Time CDF\n(Requires individual run data)",
        ha="center",
        va="center",
        fontsize=14,
    )

    ax.set_xlabel("Recovery Time (seconds)")
    ax.set_ylabel("Cumulative Probability")
    ax.set_title("Recovery Time Cumulative Distribution")

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    logger.info("Saved recovery time CDF placeholder to: %s", output_path)


def plot_token_usage_vs_success(aggregate: dict[str, Any], output_path: Path) -> None:
    """Generate scatter plot of token usage vs success rate.

    Args:
        aggregate: Aggregate statistics dictionary
        output_path: Path to save plot
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    res = aggregate.get("resources", {})
    success_rate = aggregate.get("success_rate", 0)
    tokens_mean = res.get("tokens_mean", 0)

    fig, ax = plt.subplots(figsize=(10, 6))

    # Plot point
    ax.plot(
        [tokens_mean],
        [success_rate * 100],
        "bo",
        markersize=15,
        label="Quadracode System",
    )

    ax.set_xlabel("Total Tokens (mean)")
    ax.set_ylabel("Success Rate (%)")
    ax.set_title("Token Usage vs Success Rate")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.set_ylim([0, 100])

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    logger.info("Saved token usage plot to: %s", output_path)


def plot_exhaustion_mode_frequency(aggregate: dict[str, Any], output_path: Path) -> None:
    """Generate pie chart of exhaustion mode frequencies.

    Args:
        aggregate: Aggregate statistics dictionary
        output_path: Path to save plot

    Note: This requires exhaustion mode data from individual runs
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    # Placeholder with common exhaustion modes
    modes = {
        "TEST_FAILURE": 35,
        "MISSING_ARTIFACTS": 25,
        "INCOMPLETE_EVIDENCE": 20,
        "TOOL_BACKPRESSURE": 10,
        "OTHER": 10,
    }

    fig, ax = plt.subplots(figsize=(10, 8))

    ax.pie(
        modes.values(),
        labels=modes.keys(),
        autopct="%1.1f%%",
        startangle=90,
        colors=["#ff9999", "#66b3ff", "#99ff99", "#ffcc99", "#ff99cc"],
    )

    ax.set_title("Exhaustion Mode Frequency\n(Example Distribution)")

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    logger.info("Saved exhaustion mode frequency plot to: %s", output_path)


def main() -> None:
    """Main entry point for plot generation."""
    parser = argparse.ArgumentParser(description="Generate E2E metrics visualizations")
    parser.add_argument(
        "--aggregate",
        required=True,
        help="Path to aggregate metrics JSON",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Directory to write plot files",
    )

    args = parser.parse_args()

    # Load aggregate statistics
    aggregate_path = Path(args.aggregate)
    if not aggregate_path.exists():
        logger.error("Aggregate file not found: %s", aggregate_path)
        return

    with aggregate_path.open() as f:
        aggregate = json.load(f)

    logger.info("Loaded aggregate statistics")

    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate all plots
    logger.info("Generating plots...")

    plot_false_stop_rate_by_test(aggregate, output_dir / "false_stop_rate.png")
    plot_humanclone_roc_curve(aggregate, output_dir / "humanclone_roc.png")
    plot_prp_cycle_distribution(aggregate, output_dir / "prp_cycles.png")
    plot_recovery_time_cdf(aggregate, output_dir / "recovery_time_cdf.png")
    plot_token_usage_vs_success(aggregate, output_dir / "token_usage.png")
    plot_exhaustion_mode_frequency(aggregate, output_dir / "exhaustion_modes.png")

    logger.info("Plot generation complete")
    logger.info("Plots saved to: %s", output_dir)


if __name__ == "__main__":
    main()

