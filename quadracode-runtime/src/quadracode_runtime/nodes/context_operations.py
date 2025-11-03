"""Context operation definitions shared across components."""

from enum import Enum


class ContextOperation(str, Enum):
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
