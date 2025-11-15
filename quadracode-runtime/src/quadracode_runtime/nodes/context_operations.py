"""
This module defines the `ContextOperation` enumeration, which represents the 
set of possible actions that can be applied to a context segment.

These operations are the fundamental building blocks of the context curation and 
governance processes. By defining them as a strict enumeration, this module 
ensures that all components of the context engine use a consistent and 
well-defined set of actions, which is crucial for the predictability and 
robustness of the system.
"""
from enum import Enum


class ContextOperation(str, Enum):
    """
    Enumeration of the possible operations that can be applied to a context 
    segment.

    Each member of this enum represents a specific action that the context 
    engine can take to manage the working context. These operations are used by 
    the `ContextCurator` and the context governor to optimize the context for 
    relevance, size, and quality.
    """
    RETAIN = "retain"
    COMPRESS = "compress"
    DISCARD = "discard"
    SUMMARIZE = "summarize"
    ISOLATE = "isolate"
    EVOLVE = "evolve"
    REFLECT = "reflect"
    CURATE = "curate"
    FETCH = "fetch"
    EXTERNALIZE = "externalize"
