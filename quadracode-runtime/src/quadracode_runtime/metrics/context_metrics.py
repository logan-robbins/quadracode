"""
This module provides the `ContextMetricsEmitter`, a dedicated component for 
capturing and broadcasting metrics and observability events related to the 
context engine and autonomous operations.

It offers a unified interface for emitting telemetry, which can be configured to 
either log to the console or publish to a Redis stream. This dual-mode capability 
allows for flexible deployment in different environments. The emitter is designed 
to be resilient, with best-effort Redis connection management to ensure that 
metrics emission does not interfere with the primary functions of the runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from ..config import ContextEngineConfig
from ..state import ContextEngineState

LOGGER = logging.getLogger(__name__)


class ContextMetricsEmitter:
    """
    Handles the emission of context and autonomous metrics.

    This class is responsible for publishing metrics to a configured backend, 
    which can be either the local log or a Redis stream. It manages the Redis 
    connection lifecycle and ensures that metrics are emitted in a structured, 
    JSON-serializable format.

    Attributes:
        config: A `ContextEngineConfig` instance containing the metrics 
                configuration.
    """

    def __init__(self, config: ContextEngineConfig):
        """
        Initializes the `ContextMetricsEmitter`.

        Args:
            config: The configuration for the context engine, which includes 
                    metrics settings.
        """
        self.config = config
        self._redis = None
        self._redis_loop_id: int | None = None
        self._redis_lock = asyncio.Lock()

    async def emit(
        self,
        state: ContextEngineState,
        event: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Emits a context metric event.

        This method first records the event in the local metrics log within the 
        state, and then, if enabled, publishes it to the configured backend.

        Args:
            state: The current state of the context engine.
            event: The name of the event.
            payload: A dictionary containing the event's data.
        """
        record = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        state["metrics_log"].append(record)

        if not self.config.metrics_enabled:
            return

        if self.config.metrics_emit_mode == "log":
            LOGGER.info("[context-metrics] %s", json.dumps(record))
            return

        await self._emit_redis(record, self.config.metrics_stream_key)

    async def emit_autonomous(
        self,
        event: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Emits an autonomous operation event.

        This method is specifically for events related to the autonomous loop. 
        It publishes the event to the configured backend, which can be a 
        dedicated Redis stream for autonomous events.

        Args:
            event: The name of the autonomous event.
            payload: A dictionary containing the event's data.
        """
        if not self.config.metrics_enabled:
            return

        record = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }

        if self.config.metrics_emit_mode == "log":
            LOGGER.info("[autonomous-metrics] %s", json.dumps(record))
            return

        stream_key = getattr(self.config, "autonomous_metrics_stream_key", None)
        if not stream_key:
            stream_key = self.config.metrics_stream_key

        await self._emit_redis(record, stream_key)

    async def _emit_redis(self, record: Dict[str, Any], stream_key: str) -> None:
        """
        Emits a record to the specified Redis stream.

        This private helper method handles the serialization of the record and 
        the `XADD` command to publish the event.

        Args:
            record: The event record to publish.
            stream_key: The Redis stream to publish to.
        """
        try:
            client = await self._ensure_redis()
        except Exception as exc:  # pragma: no cover - best effort
            LOGGER.warning("Failed to initialize Redis client for metrics: %s", exc)
            return

        if client is None:
            return

        try:
            payload = {
                "event": record["event"],
                "timestamp": record["timestamp"],
                "payload": json.dumps(record["payload"]),
            }
            await client.xadd(stream_key, payload)
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Failed to push context metrics to Redis: %s", exc)

    async def _ensure_redis(self):
        """
        Manages the Redis client connection, ensuring it is valid and 
        thread-safe.

        This method implements a lazy, thread-safe connection pattern for the 
        Redis client. It handles the initial connection, as well as the 
        re-establishment of the connection if the event loop changes.

        Returns:
            An active Redis client instance, or `None` if the connection could 
            not be established.
        """
        current_loop = asyncio.get_running_loop()
        if self._redis is not None and self._redis_loop_id == id(current_loop):
            return self._redis

        async with self._redis_lock:
            current_loop = asyncio.get_running_loop()
            if self._redis is not None and self._redis_loop_id == id(current_loop):
                return self._redis

            if self._redis is not None:
                try:
                    await self._redis.close()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass
                self._redis = None
                self._redis_loop_id = None

            try:
                from redis.asyncio import Redis  # type: ignore
            except ImportError:  # pragma: no cover
                LOGGER.info("redis package not available; skipping metrics stream")
                self._redis = None
                return None

            self._redis = Redis.from_url(self.config.metrics_redis_url, decode_responses=True)
            try:
                await self._redis.ping()
            except Exception:  # pragma: no cover
                LOGGER.warning("Redis metrics endpoint unreachable; disabling metrics stream")
                await self._redis.close()
                self._redis = None
                self._redis_loop_id = None
                return None

            self._redis_loop_id = id(current_loop)
            return self._redis
            
