from __future__ import annotations

import types

import pytest

from quadracode_runtime.registry import AgentRegistryIntegration


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_integration(**overrides) -> AgentRegistryIntegration:
    defaults = dict(
        agent_id="agent-test",
        host="localhost",
        port=8123,
        interval=0,
        base_url="http://agent-registry:8090",
        timeout=0.1,
        startup_timeout=0.2,
    )
    defaults.update(overrides)
    return AgentRegistryIntegration(**defaults)


@pytest.mark.anyio
async def test_agent_registry_start_times_out_when_registration_never_succeeds():
    integration = _make_integration(startup_timeout=0.2, interval=0)

    async def failing_register(self):
        return False

    integration._register = types.MethodType(failing_register, integration)  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError):
        try:
            await integration.start()
        finally:
            await integration.shutdown()


@pytest.mark.anyio
async def test_agent_registry_start_retries_until_success():
    integration = _make_integration(startup_timeout=1, interval=0)

    attempts = 0

    async def flaky_register(self):
        nonlocal attempts
        attempts += 1
        return attempts >= 3

    async def fake_heartbeat(self):
        return True

    integration._register = types.MethodType(flaky_register, integration)  # type: ignore[attr-defined]
    integration._heartbeat = types.MethodType(fake_heartbeat, integration)  # type: ignore[attr-defined]

    await integration.start()
    assert attempts == 3
    await integration.shutdown()

