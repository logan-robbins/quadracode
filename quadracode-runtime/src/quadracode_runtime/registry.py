from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Optional

from quadracode_tools.tools.agent_registry import agent_registry_tool

LOGGER = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        LOGGER.warning("Invalid integer for %s=%s; using %d", name, raw, default)
        return default


class AgentRegistryIntegration:
    """Manage agent registration and heartbeat lifecycle with the registry service."""

    def __init__(self, agent_id: str, *, host: str, port: int, interval: int) -> None:
        self._agent_id = agent_id
        self._host = host
        self._port = port
        self._interval = max(5, interval)
        self._registered = False
        self._task: Optional[asyncio.Task[None]] = None

    @classmethod
    def from_environment(cls, profile_name: str, agent_id: str) -> Optional["AgentRegistryIntegration"]:
        if profile_name != "agent":
            return None
        if not _env_flag("QUADRACODE_AGENT_AUTOREGISTER", True):
            LOGGER.info("Agent auto-registration disabled via environment")
            return None

        host = (
            os.environ.get("QUADRACODE_AGENT_HOST")
            or os.environ.get("AGENT_HOST")
            or os.environ.get("HOSTNAME")
            or agent_id
        )
        port = _env_int("QUADRACODE_AGENT_PORT", 8123)
        interval = _env_int("QUADRACODE_AGENT_HEARTBEAT_INTERVAL", 15)
        return cls(agent_id, host=host, port=port, interval=interval)

    async def start(self) -> None:
        if self._task:
            return
        success = await self._register()
        if not success:
            LOGGER.warning("Initial agent registry registration failed; will retry in heartbeat loop")
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def shutdown(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._registered:
            await self._unregister()

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                if not self._registered:
                    await self._register()
                else:
                    success = await self._heartbeat()
                    if not success:
                        self._registered = False
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            raise

    async def _register(self) -> bool:
        payload = {
            "operation": "register_agent",
            "agent_id": self._agent_id,
            "host": self._host,
            "port": self._port,
        }
        response = await agent_registry_tool.ainvoke(payload)
        if _looks_like_error(response):
            LOGGER.warning("Agent registry registration error: %s", response)
            self._registered = False
            return False
        LOGGER.info(
            "Registered agent %s with registry (%s:%s)",
            self._agent_id,
            self._host,
            self._port,
        )
        self._registered = True
        return True

    async def _heartbeat(self) -> bool:
        payload = {
            "operation": "heartbeat",
            "agent_id": self._agent_id,
            "status": "healthy",
        }
        response = await agent_registry_tool.ainvoke(payload)
        if _looks_like_error(response):
            LOGGER.warning("Agent heartbeat failed: %s", response)
            return False
        LOGGER.debug("Heartbeat acknowledged for agent %s", self._agent_id)
        return True

    async def _unregister(self) -> None:
        payload = {"operation": "unregister_agent", "agent_id": self._agent_id}
        response = await agent_registry_tool.ainvoke(payload)
        if _looks_like_error(response):
            LOGGER.warning("Agent unregister failed: %s", response)
        else:
            LOGGER.info("Unregistered agent %s from registry", self._agent_id)
        self._registered = False


def _looks_like_error(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return True
    return lowered.startswith("registry request failed") or lowered.startswith("unable to reach")
