from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List

import pytest

from quadracode_tools.tools.workspace import ensure_workspace, workspace_destroy


def _run(command: List[str], *, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command {' '.join(command)} failed: {result.stderr.strip()}")
    return result


@pytest.mark.e2e
def test_agent_spawn_inherits_workspace_volume(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI must be installed and available on PATH for workspace mount test")

    agent_image = "quadracode-agent"
    image_check = subprocess.run(["docker", "image", "inspect", agent_image], capture_output=True, text=True)
    if image_check.returncode != 0:
        build = subprocess.run(["docker", "compose", "build", "agent-runtime"], capture_output=True, text=True)
        if build.returncode != 0:
            pytest.fail(
                f"Failed to build agent image (docker compose build agent-runtime).\nstdout: {build.stdout}\nstderr: {build.stderr}"
            )

    workspace_id = f"ws-test-{int(time.time())}"
    success, descriptor, error = ensure_workspace(workspace_id, image="python:3.12-slim", network="bridge")
    if not success or descriptor is None:
        pytest.fail(f"Unable to provision workspace for test: {error}")

    agent_id = None
    agent_container = None
    try:
        env = os.environ.copy()
        env.update(
            {
                "QUADRACODE_WORKSPACE_VOLUME": descriptor.volume,
                "QUADRACODE_WORKSPACE_ID": workspace_id,
                "QUADRACODE_WORKSPACE_MOUNT": descriptor.mount_path,
            }
        )
        spawn_script = Path("scripts/agent-management/spawn-agent.sh")
        result = _run([str(spawn_script), "", agent_image, "bridge"], env=env, check=False)
        if result.returncode != 0:
            pytest.fail(f"Unable to spawn agent container: {result.stderr.strip() or result.stdout}")

        payload = json.loads(result.stdout)
        assert payload.get("success"), f"Spawn script reported failure: {payload}"
        agent_id = payload.get("agent_id")
        agent_container = payload.get("container_name")
        assert agent_container, "Spawn script did not return container name"

        inspect = _run(
            [
                "docker",
                "inspect",
                agent_container,
                "--format",
                "{{json .Mounts}}",
            ]
        )
        mounts = json.loads(inspect.stdout)
        has_workspace_mount = any(
            mount.get("Destination") == descriptor.mount_path and mount.get("Name") == descriptor.volume for mount in mounts
        )
        assert has_workspace_mount, f"Workspace volume {descriptor.volume} not mounted at {descriptor.mount_path} in {agent_container}"
    finally:
        if agent_container:
            subprocess.run(["docker", "rm", "-f", agent_container], capture_output=True)
        workspace_destroy.invoke({"workspace_id": workspace_id, "delete_volume": True})
