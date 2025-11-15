"""
This package provides the utilities for emitting metrics and observability 
events from the Quadracode runtime.

It is designed to be a centralized hub for all telemetry-related functionality, 
allowing the runtime to report on its internal state and performance. The 
components in this package, such as the `ContextMetricsEmitter`, provide a 
structured way to capture and broadcast key events, which is essential for 
monitoring, debugging, and analyzing the behavior of the system.
"""
from .context_metrics import ContextMetricsEmitter

__all__ = ["ContextMetricsEmitter"]
