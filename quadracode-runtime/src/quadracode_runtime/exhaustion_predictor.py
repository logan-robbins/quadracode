"""
This module implements the `ExhaustionPredictor`, a component that uses a simple 
machine learning model to forecast the likelihood of "exhaustion" in the 
Plan-Refine-Play (PRP) loop.

Exhaustion events are critical signals in the autonomous workflow, indicating that 
the system is stuck or has reached a point of diminishing returns. This predictor 
analyzes the history of the refinement ledger to learn the patterns that precede 
these events. By training a logistic regression model on a set of engineered 
features, it can provide a probabilistic forecast of whether the next cycle is 
likely to result in exhaustion. This predictive capability allows the orchestrator 
to take proactive recovery actions, such as preemptively refining its hypothesis, 
before the exhaustion event actually occurs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Iterable, List, Sequence

import numpy as np

try:  # pragma: no cover - import guard exercised at runtime
    from sklearn.linear_model import LogisticRegression
except ModuleNotFoundError as exc:  # pragma: no cover - surfaced during installation
    raise RuntimeError(
        "scikit-learn is required for exhaustion prediction. Install quadracode-runtime"
        " with the optional 'predictor' dependencies."
    ) from exc

from .state import ExhaustionMode, RefinementLedgerEntry


def _is_failure_status(status: str | None) -> bool:
    if not status:
        return False
    lowered = status.lower()
    return any(keyword in lowered for keyword in {"fail", "reject", "error", "halt"})


def _is_success_status(status: str | None) -> bool:
    if not status:
        return False
    lowered = status.lower()
    return any(keyword in lowered for keyword in {"success", "pass", "complete", "resolved"})


def _has_exhaustion(entry: RefinementLedgerEntry | None) -> bool:
    if entry is None:
        return False
    trigger = entry.exhaustion_trigger
    return trigger is not None and trigger is not ExhaustionMode.NONE


@dataclass(slots=True)
class ExhaustionPredictor:
    """
    Trains and uses a simple logistic regression model to forecast the 
    likelihood of an exhaustion event in the PRP loop.

    This class encapsulates the entire lifecycle of the exhaustion predictor, 
    from feature engineering and model training to prediction. It is designed to 
    be a self-contained component that can be easily integrated into the main 
    runtime.

    Attributes:
        threshold: The probability threshold for preemptive action.
        max_history: The maximum number of ledger entries to use for training.
        solver: The solver to use for the logistic regression model.
    """

    threshold: float = 0.7
    max_history: int = 128
    solver: str = "liblinear"
    _model: LogisticRegression | None = field(default=None, init=False)
    _class_prior: float = field(default=0.0, init=False)
    _trained: bool = field(default=False, init=False)
    _last_trained_size: int = field(default=0, init=False)

    def fit(self, ledger: Sequence[RefinementLedgerEntry]) -> None:
        """
        Fits (or re-fits) the logistic regression model using the provided 
        ledger history.

        This method builds a dataset from the ledger, engineers a set of 
        features, and then trains a `LogisticRegression` model. The model is 
        stored internally for later use in prediction.

        Args:
            ledger: A sequence of `RefinementLedgerEntry` objects.
        """

        dataset, labels = self._build_dataset(ledger)
        if not dataset or len(set(labels)) < 2:
            self._model = None
            self._class_prior = float(sum(labels)) / len(labels) if labels else 0.0
            self._trained = False
            self._last_trained_size = len(ledger)
            return

        model = LogisticRegression(
            solver=self.solver,
            max_iter=1000,
            class_weight="balanced",
        )
        features = np.array(dataset, dtype=float)
        targets = np.array(labels, dtype=int)
        model.fit(features, targets)
        self._model = model
        self._class_prior = float(targets.mean())
        self._trained = True
        self._last_trained_size = len(ledger)

    def predict_probability(
        self, ledger: Sequence[RefinementLedgerEntry]
    ) -> float:
        """
        Predicts the probability that the next cycle will result in an 
        exhaustion event.

        This method first ensures that the model is trained on the latest 
        ledger data, and then uses the model to generate a probability for the 
        current state.

        Args:
            ledger: The current sequence of `RefinementLedgerEntry` objects.

        Returns:
            A probability between 0.0 and 1.0.
        """

        if not ledger:
            return self._class_prior

        if not self._trained or len(ledger) != self._last_trained_size:
            self.fit(ledger)
            if not self._trained:
                return self._class_prior

        assert self._model is not None  # for type checkers
        features = np.array([self._compute_features(ledger)], dtype=float)
        probability = float(self._model.predict_proba(features)[0][1])
        return float(min(1.0, max(0.0, probability)))

    def should_preempt(self, ledger: Sequence[RefinementLedgerEntry]) -> bool:
        """
        Determines whether the orchestrator should take preemptive action to 
        avoid an impending exhaustion event.

        Args:
            ledger: The current sequence of `RefinementLedgerEntry` objects.

        Returns:
            True if the predicted probability of exhaustion exceeds the configured 
            threshold.
        """

        return self.predict_probability(ledger) >= self.threshold

    def _build_dataset(
        self, ledger: Sequence[RefinementLedgerEntry]
    ) -> tuple[List[List[float]], List[int]]:
        """Builds a feature dataset and corresponding labels from the ledger."""
        dataset: List[List[float]] = []
        labels: List[int] = []
        history: List[RefinementLedgerEntry] = []
        for entry in ledger:
            dataset.append(self._compute_features(history))
            labels.append(1 if _has_exhaustion(entry) else 0)
            history.append(entry)
        return dataset, labels

    def _compute_features(
        self, history: Sequence[RefinementLedgerEntry]
    ) -> List[float]:
        """
        Engineers a set of features from the history of the refinement ledger.

        These features are designed to capture the key signals that are 
        indicative of impending exhaustion, such as the frequency of recent 
        failures, the number of consecutive exhaustions, and the complexity of 
        the hypotheses.
        """
        if not history:
            return [0.0] * 12

        window = list(history[-self.max_history :])
        total = len(window)

        exhaustion_flags = [_has_exhaustion(entry) for entry in window]
        failure_flags = [_is_failure_status(entry.status) for entry in window]
        success_flags = [_is_success_status(entry.status) for entry in window]

        recent_window = window[-min(3, total) :]
        recent_exhaustion = [_has_exhaustion(entry) for entry in recent_window]
        recent_failure = [_is_failure_status(entry.status) for entry in recent_window]

        hypothesis_lengths = [len(entry.hypothesis or "") for entry in window]
        outcome_lengths = [len(entry.outcome_summary or "") for entry in window]

        consecutive_exhaustion = 0
        for entry in reversed(window):
            if _has_exhaustion(entry):
                consecutive_exhaustion += 1
            else:
                break

        consecutive_failure = 0
        for entry in reversed(window):
            if _is_failure_status(entry.status):
                consecutive_failure += 1
            else:
                break

        time_since_exhaustion = 0
        for entry in reversed(window):
            time_since_exhaustion += 1
            if _has_exhaustion(entry):
                break
        else:
            time_since_exhaustion = total + 1

        return [
            float(total),
            float(sum(exhaustion_flags)) / total,
            float(sum(recent_exhaustion)) / len(recent_window),
            float(sum(failure_flags)) / total,
            float(sum(recent_failure)) / len(recent_window),
            float(mean(hypothesis_lengths)),
            float(mean(outcome_lengths)),
            float(pstdev(outcome_lengths)) if total > 1 else 0.0,
            float(consecutive_exhaustion),
            float(consecutive_failure),
            float(time_since_exhaustion),
            float(sum(success_flags)) / total,
        ]
