"""
This module provides the `AgentRegistryIntegration` class, which is responsible 
for managing an agent's lifecycle with the central agent registry service.

This component handles the initial registration of the agent, the periodic 
sending of heartbeats to keep the registration alive, and the final 
unregistration on shutdown. It is designed to be a self-contained, asynchronous 
component that can be easily integrated into the main agent runtime. The 
integration is configurable via a set of environment variables, allowing for 
flexible deployment in different environments.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone
import time
from typing import Optional

import httpx

LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_STARTUP_TIMEOUT_SECONDS = 60.0


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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        LOGGER.warning("Invalid float for %s=%s; using %.2f", name, raw, default)
        return default


class AgentRegistryIntegration:
    """
    Manages the registration and heartbeat lifecycle of an agent with the 
    central registry service.

    This class encapsulates all the logic for communicating with the agent 
    registry, including registration, heartbeats, and unregistration. It runs 
    as an asynchronous task, periodically sending heartbeats to the registry to 
    signal that the agent is still alive.

    Attributes:
        _agent_id: The unique ID of the agent.
        ... and other configuration parameters.
    """

    def __init__(
        self,
        agent_id: str,
        *,
        host: str,
        port: int,
        interval: int,
        base_url: str,
        timeout: float,
        startup_timeout: float,
    ) -> None:
        """
        Initializes the `AgentRegistryIntegration`.

        Args:
            agent_id: The unique ID of the agent.
            host: The hostname or IP address of the agent.
            port: The port on which the agent is running.
            interval: The interval in seconds for sending heartbeats.
            base_url: The base URL of the agent registry service.
            timeout: The timeout in seconds for requests to the registry.
            startup_timeout: Seconds to wait for the initial registration before failing.
        """
        self._agent_id = agent_id
        self._host = host
        self._port = port
        self._interval = max(0.1, interval)
        self._registered = False
        self._task: Optional[asyncio.Task[None]] = None
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout if timeout > 0 else DEFAULT_TIMEOUT_SECONDS
        self._client: Optional[httpx.AsyncClient] = None
        self._startup_timeout = startup_timeout if startup_timeout > 0 else 0.0

    @classmethod
    def from_environment(cls, profile_name: str, agent_id: str) -> Optional["AgentRegistryIntegration"]:
        """
        Creates an `AgentRegistryIntegration` instance from environment 
        variables.

        This factory method is the preferred way to create an instance of this 
        class. It reads all the necessary configuration from a predefined set of 
        environment variables, and it will return `None` if the integration is 
        disabled or if the profile is not an agent profile.

        Args:
            profile_name: The name of the runtime profile.
            agent_id: The unique ID of the agent.

        Returns:
            An instance of `AgentRegistryIntegration`, or `None` if the 
            integration is not applicable.
        """
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
        timeout = _env_float("QUADRACODE_AGENT_REGISTRY_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
        startup_timeout = _env_float(
            "QUADRACODE_AGENT_REGISTRATION_TIMEOUT", DEFAULT_STARTUP_TIMEOUT_SECONDS
        )
        base_url = os.environ.get("AGENT_REGISTRY_URL", "http://quadracode-agent-registry:8090")
        return cls(
            agent_id,
            host=host,
            port=port,
            interval=interval,
            base_url=base_url,
            timeout=timeout,
            startup_timeout=startup_timeout,
        )

    async def start(self) -> None:
        """
        Starts the agent registration and heartbeat loop.

        This method performs the initial registration with the registry and then 
        starts the background task that sends periodic heartbeats.
        """
        if self._task:
            return
        LOGGER.info(
            "Agent registry integration starting (agent_id=%s, base_url=%s, timeout=%.1fs)",
            self._agent_id,
            self._base_url,
            self._startup_timeout,
        )
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        await self._ensure_initial_registration()
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def shutdown(self) -> None:
        """
        Shuts down the heartbeat loop and unregisters the agent.
        """
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._registered:
            await self._unregister()
        if self._client:
            await self._client.aclose()
            self._client = None

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
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.warning("Agent registry heartbeat loop stopped due to error: %s", exc)

    async def _ensure_initial_registration(self) -> None:
        success = await self._register()
        if success or self._startup_timeout <= 0:
            return

        deadline = time.monotonic() + self._startup_timeout
        attempt = 1
        while time.monotonic() < deadline:
            attempt += 1
            await asyncio.sleep(min(self._interval, 5))
            success = await self._register()
            if success:
                LOGGER.info(
                    "Agent registry registration succeeded on retry %d for %s",
                    attempt,
                    self._agent_id,
                )
                return

        raise RuntimeError(
            f"Agent registry registration failed after {attempt} attempts "
            f"({self._startup_timeout:.1f}s timeout)"
        )

    async def _register(self) -> bool:
        LOGGER.info(
            "Registering agent_id=%s host=%s port=%s base_url=%s",
            self._agent_id,
            self._host,
            self._port,
            self._base_url,
        )
        success = await self._request(
            "POST",
            "/agents/register",
            {
                "agent_id": self._agent_id,
                "host": self._host,
                "port": self._port,
            },
        )
        if success:
            LOGGER.info(
                "Registered agent %s with registry (%s:%s)",
                self._agent_id,
                self._host,
                self._port,
            )
            self._registered = True
            return True
        LOGGER.warning(
            "Agent registry registration failed for %s (host=%s port=%s)",
            self._agent_id,
            self._host,
            self._port,
        )
        self._registered = False
        return False

    async def _heartbeat(self) -> bool:
        LOGGER.debug("Agent registry heartbeat agent_id=%s", self._agent_id)
        success = await self._request(
            "POST",
            f"/agents/{self._agent_id}/heartbeat",
            {
                "agent_id": self._agent_id,
                "status": "healthy",
                "reported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        if not success:
            LOGGER.warning("Agent heartbeat failed for %s", self._agent_id)
        return bool(success)

    async def _unregister(self) -> None:
        success = await self._request("DELETE", f"/agents/{self._agent_id}")
        if success:
            LOGGER.info("Unregistered agent %s from registry", self._agent_id)
        else:
            LOGGER.warning("Agent unregister failed for %s", self._agent_id)
        self._registered = False

    async def _request(self, method: str, path: str, payload: Optional[dict] = None) -> bool:
        if not self._client:
            raise RuntimeError("Agent registry client not initialized")
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.request(method, url, json=payload)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            LOGGER.warning("Agent registry %s %s failed: %s", method.upper(), url, exc)
            return False
