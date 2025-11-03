"""Context metrics emission helpers."""

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
    """Publishes context metrics to Redis streams and local buffers."""

    def __init__(self, config: ContextEngineConfig):
        self.config = config
        self._redis = None
        self._redis_lock = asyncio.Lock()

    async def emit(
        self,
        state: ContextEngineState,
        event: str,
        payload: Dict[str, Any],
    ) -> None:
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
        if self._redis is not None:
            return self._redis

        async with self._redis_lock:
            if self._redis is not None:
                return self._redis

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
            return self._redis
