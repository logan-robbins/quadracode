from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field, validator

from quadracode_contracts import (
    DEFAULT_WORKSPACE_MOUNT,
    WorkspaceCommandResult,
    WorkspaceCopyResult,
    WorkspaceDescriptor,
    collect_environment_keys,
    normalize_workspace_name,
)

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover - redis presence depends on environment
    redis = None


DOCKER_BIN = os.environ.get("QUADRACODE_DOCKER_BIN", "docker")
DEFAULT_IMAGE = os.environ.get("QUADRACODE_WORKSPACE_IMAGE", "quadracode-workspace:latest")
DEFAULT_NETWORK = os.environ.get("QUADRACODE_WORKSPACE_NETWORK", "quadracode_default")
LOGS_DIR = f"{DEFAULT_WORKSPACE_MOUNT}/logs"
WORKSPACE_STREAM_PREFIX = os.environ.get("QUADRACODE_WORKSPACE_STREAM_PREFIX", "qc:workspace")
WORKSPACE_REDIS_URL = os.environ.get(
    "QUADRACODE_WORKSPACE_REDIS_URL",
    os.environ.get("QUADRACODE_METRICS_REDIS_URL", "redis://redis:6379/0"),
)
_WORKSPACE_EVENTS_DISABLED = False
_WORKSPACE_REDIS_CLIENT: Optional["redis.Redis"] = None  # type: ignore[name-defined]
_WORKSPACE_EVENTS_LOCK = Lock()


class WorkspaceError(RuntimeError):
    """Workspace operation failed."""


class WorkspaceBaseRequest(BaseModel):
    workspace_id: str = Field(..., description="Workspace identifier (usually the chat_id).")

    @validator("workspace_id")
    def _validate_id(cls, value: str) -> str:  # type: ignore[override]
        if not value or not value.strip():
            raise ValueError("workspace_id must be a non-empty string")
        return value.strip()


class WorkspaceCreateRequest(WorkspaceBaseRequest):
    image: Optional[str] = Field(
        default=None,
        description="Docker image to use for the workspace container (overrides default).",
    )
    network: Optional[str] = Field(
        default=None,
        description="Optional Docker network for the workspace container.",
    )


class WorkspaceExecRequest(WorkspaceBaseRequest):
    command: str = Field(..., description="Shell command to execute inside the workspace container.")
    working_dir: Optional[str] = Field(
        default=None,
        description="Working directory for the command (defaults to /workspace).",
    )
    environment: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional environment variables for the command.",
    )
    timeout: Optional[float] = Field(
        default=None,
        description="Optional timeout in seconds for the command.",
    )

    @validator("command")
    def _validate_command(cls, value: str) -> str:  # type: ignore[override]
        if not value or not value.strip():
            raise ValueError("command must be a non-empty string")
        return value


class WorkspaceCopyToRequest(WorkspaceBaseRequest):
    source_path: str = Field(..., description="Path on the host to copy into the workspace.")
    destination_path: Optional[str] = Field(
        default=None,
        description="Destination path inside the workspace (defaults to /workspace).",
    )


class WorkspaceCopyFromRequest(WorkspaceBaseRequest):
    source_path: str = Field(..., description="Path inside the workspace to copy out.")
    destination_path: str = Field(..., description="Destination path on the host.")


class WorkspaceDestroyRequest(WorkspaceBaseRequest):
    delete_volume: bool = Field(
        default=True,
        description="Whether to delete the workspace volume after stopping the container.",
    )


class WorkspaceInfoRequest(WorkspaceBaseRequest):
    include_volume_usage: bool = Field(
        default=False,
        description="Attempt to compute volume disk usage (requires temporary container).",
    )


@dataclass(frozen=True)
class WorkspaceResources:
    workspace_id: str
    slug: str
    volume: str
    container: str


def _get_workspace_event_client() -> Optional["redis.Redis"]:  # type: ignore[name-defined]
    global _WORKSPACE_REDIS_CLIENT, _WORKSPACE_EVENTS_DISABLED
    if _WORKSPACE_EVENTS_DISABLED or WORKSPACE_REDIS_URL in {"", None}:  # type: ignore[comparison-overlap]
        return None
    if redis is None:  # pragma: no cover - depends on optional dependency
        _WORKSPACE_EVENTS_DISABLED = True
        return None
    client = _WORKSPACE_REDIS_CLIENT
    if client is not None:
        return client
    with _WORKSPACE_EVENTS_LOCK:
        if _WORKSPACE_REDIS_CLIENT is not None:
            return _WORKSPACE_REDIS_CLIENT
        try:
            _WORKSPACE_REDIS_CLIENT = redis.Redis.from_url(  # type: ignore[attr-defined]
                WORKSPACE_REDIS_URL,
                decode_responses=True,
            )
        except Exception:  # pragma: no cover - connection failure
            _WORKSPACE_EVENTS_DISABLED = True
            return None
    return _WORKSPACE_REDIS_CLIENT


def _publish_workspace_event(workspace_id: str, event: str, payload: Dict[str, Any]) -> None:
    client = _get_workspace_event_client()
    if client is None:
        return
    stream_key = f"{WORKSPACE_STREAM_PREFIX}:{workspace_id}:events"
    record = {
        "event": event,
        "timestamp": _now_iso(),
        "payload": json.dumps(payload, separators=(",", ":")),
    }
    try:
        client.xadd(stream_key, record, maxlen=5000, approximate=True)
    except Exception:  # pragma: no cover - Redis failure
        global _WORKSPACE_EVENTS_DISABLED
        _WORKSPACE_EVENTS_DISABLED = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _workspace_resources(workspace_id: str) -> WorkspaceResources:
    slug = normalize_workspace_name(workspace_id)
    volume = f"qc-ws-{slug}"
    container = f"{volume}-ctr"
    return WorkspaceResources(workspace_id=workspace_id, slug=slug, volume=volume, container=container)


def ensure_workspace(
    workspace_id: str,
    *,
    image: Optional[str] = None,
    network: Optional[str] = None,
) -> Tuple[bool, Optional[WorkspaceDescriptor], Optional[str]]:
    """Ensure the workspace container and volume exist and are running."""

    resources = _workspace_resources(workspace_id)
    selected_image = image or DEFAULT_IMAGE
    if not selected_image:
        return False, None, "Workspace image not provided"
    selected_network = network or DEFAULT_NETWORK

    volume_preexists = _volume_exists(resources.volume)
    if not volume_preexists:
        result = _run_docker(["volume", "create", resources.volume])
        if result.returncode != 0:
            stderr = result.stderr.strip() or "unknown docker error"
            return False, None, f"Failed to create volume {resources.volume}: {stderr}"

    container_preexists = _container_exists(resources.container)
    container_was_running = False
    container_data: Optional[Dict[str, Any]] = None

    if container_preexists:
        container_data = _inspect_container(resources.container)
        container_was_running = _container_running(container_data)
        if not container_was_running:
            start_result = _run_docker(["container", "start", resources.container])
            if start_result.returncode != 0:
                stderr = start_result.stderr.strip() or "unknown docker error"
                return False, None, f"Failed to start existing container {resources.container}: {stderr}"
    else:
        try:
            _start_container(resources, selected_image, selected_network)
        except WorkspaceError as exc:
            return False, None, str(exc)

    if container_data is None or not _container_running(container_data):
        container_data = _inspect_container(resources.container)

    descriptor = _workspace_descriptor(resources, container_data=container_data)
    event_name = "workspace_ready"
    if not container_preexists:
        event_name = "workspace_created"
    elif not container_was_running:
        event_name = "workspace_started"

    _publish_workspace_event(
        workspace_id,
        event_name,
        {
            "workspace": descriptor.dict(),
            "volume_created": not volume_preexists,
            "container_preexists": container_preexists,
            "previously_running": container_was_running,
            "image": descriptor.image,
        },
    )
    return True, descriptor, None


def _run_docker(
    args: Sequence[str],
    *,
    timeout: Optional[float] = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [DOCKER_BIN, *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )
        return result
    except FileNotFoundError as exc:  # pragma: no cover - depends on environment
        raise WorkspaceError(f"Docker binary not found at '{DOCKER_BIN}'") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"Docker command timed out: {' '.join(command)}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "unknown docker error"
        raise WorkspaceError(f"Docker command failed: {stderr}") from exc


def _run_docker_json(
    args: Sequence[str],
    *,
    timeout: Optional[float] = None,
) -> Any:
    result = _run_docker(args, timeout=timeout)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown docker error"
        raise WorkspaceError(f"Docker command failed: {stderr}")
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise WorkspaceError(f"Failed to parse docker output as JSON: {exc}") from exc


def _volume_exists(name: str) -> bool:
    result = _run_docker(["volume", "inspect", name])
    return result.returncode == 0


def _container_exists(name: str) -> bool:
    result = _run_docker(["container", "inspect", name])
    return result.returncode == 0


def _container_running(data: Dict[str, Any]) -> bool:
    state = data.get("State", {})
    return bool(state.get("Running"))


def _inspect_container(name: str) -> Dict[str, Any]:
    data = _run_docker_json(["container", "inspect", name])
    if not data:
        raise WorkspaceError(f"Container {name} not found")
    return data[0]


def _inspect_volume(name: str) -> Dict[str, Any]:
    data = _run_docker_json(["volume", "inspect", name])
    if not data:
        raise WorkspaceError(f"Volume {name} not found")
    return data[0]


def _format_timestamp(raw: Optional[str]) -> str:
    if not raw:
        return _now_iso()
    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return raw
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _workspace_descriptor(
    resources: WorkspaceResources,
    container_data: Optional[Dict[str, Any]] = None,
) -> WorkspaceDescriptor:
    if container_data is None:
        container_data = _inspect_container(resources.container)
    created_at = _format_timestamp(container_data.get("Created"))
    image = container_data.get("Config", {}).get("Image", "")
    mounts = container_data.get("Mounts", [])
    mount_path = DEFAULT_WORKSPACE_MOUNT
    for mount in mounts:
        if mount.get("Name") == resources.volume and mount.get("Destination"):
            mount_path = mount["Destination"]
            break
    descriptor = WorkspaceDescriptor(
        workspace_id=resources.workspace_id,
        volume=resources.volume,
        container=resources.container,
        mount_path=mount_path,
        image=image,
        created_at=created_at,
        container_id=container_data.get("Id"),
        state=container_data.get("State", {}),
    )
    return descriptor


def _start_container(resources: WorkspaceResources, image: str, network: Optional[str]) -> None:
    command = [
        "run",
        "-d",
        "--name",
        resources.container,
        "--mount",
        f"type=volume,source={resources.volume},target={DEFAULT_WORKSPACE_MOUNT}",
        "-e",
        f"WORKSPACE_ID={resources.workspace_id}",
        "-e",
        f"WORKSPACE_MOUNT={DEFAULT_WORKSPACE_MOUNT}",
        "--restart",
        "unless-stopped",
    ]
    if network:
        command.extend(["--network", network])
    command.append(image)
    command.extend(["sleep", "infinity"])
    result = _run_docker(command)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown docker error"
        raise WorkspaceError(f"Failed to start workspace container: {stderr}")


def _prepare_logs_directory(resources: WorkspaceResources) -> None:
    _run_docker(["exec", resources.container, "mkdir", "-p", LOGS_DIR], check=True)


def _write_logs_to_workspace(
    resources: WorkspaceResources,
    stdout_text: str,
    stderr_text: str,
    log_prefix: str,
) -> tuple[str, str, Optional[str]]:
    stdout_log = f"{LOGS_DIR}/{log_prefix}.stdout"
    stderr_log = f"{LOGS_DIR}/{log_prefix}.stderr"
    bundle_path = f"{LOGS_DIR}/{log_prefix}.log"
    with tempfile.NamedTemporaryFile(delete=False) as stdout_file:
        stdout_file.write(stdout_text.encode("utf-8"))
        stdout_tmp = Path(stdout_file.name)
    with tempfile.NamedTemporaryFile(delete=False) as stderr_file:
        stderr_file.write(stderr_text.encode("utf-8"))
        stderr_tmp = Path(stderr_file.name)

    combined_tmp: Optional[Path]
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, prefix="qc-workspace-", suffix=".log", mode="w", encoding="utf-8"
        ) as combined_file:
            combined_file.write("COMMAND STDOUT\n")
            combined_file.write(stdout_text)
            combined_file.write("\n\nCOMMAND STDERR\n")
            combined_file.write(stderr_text)
            combined_tmp = Path(combined_file.name)
    except Exception:
        combined_tmp = None

    try:
        _run_docker(["cp", str(stdout_tmp), f"{resources.container}:{stdout_log}"], check=True)
        _run_docker(["cp", str(stderr_tmp), f"{resources.container}:{stderr_log}"], check=True)
        if combined_tmp:
            _run_docker(["cp", str(combined_tmp), f"{resources.container}:{bundle_path}"], check=True)
    finally:
        stdout_tmp.unlink(missing_ok=True)
        stderr_tmp.unlink(missing_ok=True)
        if combined_tmp:
            combined_tmp.unlink(missing_ok=True)

    return stdout_log, stderr_log, bundle_path if combined_tmp else None


def _json_success(**payload: Any) -> str:
    payload["success"] = True
    return json.dumps(payload, indent=2)


def _json_error(message: str, **payload: Any) -> str:
    payload.setdefault("success", False)
    payload["error"] = message
    return json.dumps(payload, indent=2)


@tool(args_schema=WorkspaceCreateRequest)
def workspace_create(workspace_id: str, image: Optional[str] = None, network: Optional[str] = None) -> str:
    """Create (or reuse) the workspace container and volume for a chat."""

    success, descriptor, error = ensure_workspace(workspace_id, image=image, network=network)
    if not success or descriptor is None:
        return _json_error(error or "Failed to provision workspace", workspace_id=workspace_id)

    return _json_success(workspace=descriptor.dict(), message="workspace_ready")


def _build_exec_command(command: str) -> List[str]:
    return ["bash", "-lc", f"set -o pipefail; {command}"]


@tool(args_schema=WorkspaceExecRequest)
def workspace_exec(
    workspace_id: str,
    command: str,
    working_dir: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> str:
    """Execute a shell command inside the workspace container."""

    resources = _workspace_resources(workspace_id)
    exec_cwd = working_dir or DEFAULT_WORKSPACE_MOUNT
    env_keys = collect_environment_keys(environment)

    success, descriptor_obj, error = ensure_workspace(workspace_id)
    if not success or descriptor_obj is None:
        return _json_error(error or f"Workspace {workspace_id} is unavailable", workspace_id=workspace_id)
    descriptor = descriptor_obj

    exec_args: List[str] = ["exec", "-i"]
    exec_args.extend(["-w", exec_cwd])
    if environment:
        for key, value in environment.items():
            exec_args.extend(["-e", f"{key}={value}"])
    exec_args.append(resources.container)
    exec_args.extend(_build_exec_command(command))

    started_at = datetime.now(timezone.utc)
    try:
        result = _run_docker(exec_args, timeout=timeout)
    except WorkspaceError as exc:
        return _json_error(str(exc), workspace=descriptor.dict())
    finished_at = datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds()

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    stdout_bytes = len(stdout.encode("utf-8"))
    stderr_bytes = len(stderr.encode("utf-8"))

    log_prefix = f"exec-{started_at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    stdout_log: Optional[str] = None
    stderr_log: Optional[str] = None
    bundle_log: Optional[str] = None
    try:
        _prepare_logs_directory(resources)
        stdout_log, stderr_log, bundle_log = _write_logs_to_workspace(
            resources,
            stdout,
            stderr,
            log_prefix,
        )
    except WorkspaceError:
        pass

    command_result = WorkspaceCommandResult(
        workspace=descriptor,
        command=command,
        working_dir=exec_cwd,
        environment_keys=env_keys,
        started_at=started_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        finished_at=finished_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        duration_seconds=duration,
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdout_log_path=stdout_log,
        stderr_log_path=stderr_log,
        log_bundle_path=bundle_log,
    )

    event_payload: Dict[str, Any] = {
        "command": command,
        "working_dir": exec_cwd,
        "environment_keys": env_keys,
        "returncode": result.returncode,
        "duration_seconds": duration,
        "started_at": command_result.started_at,
        "finished_at": command_result.finished_at,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
    }
    if stdout_log:
        event_payload["stdout_log_path"] = stdout_log
    if stderr_log:
        event_payload["stderr_log_path"] = stderr_log
    if bundle_log:
        event_payload["log_bundle_path"] = bundle_log
    _publish_workspace_event(workspace_id, "command_executed", event_payload)

    return json.dumps(
        {
            "success": result.returncode == 0,
            "workspace_command": command_result.dict(),
        },
        indent=2,
    )


@tool(args_schema=WorkspaceCopyToRequest)
def workspace_copy_to(
    workspace_id: str,
    source_path: str,
    destination_path: Optional[str] = None,
) -> str:
    """Copy a file or directory from the host into the workspace volume."""

    resources = _workspace_resources(workspace_id)
    target_path = destination_path or DEFAULT_WORKSPACE_MOUNT
    host_path = Path(source_path)
    if not host_path.exists():
        return _json_error("Source path does not exist", source_path=str(host_path))
    bytes_transferred: Optional[int] = None
    if host_path.is_file():
        bytes_transferred = host_path.stat().st_size

    success, descriptor_obj, error = ensure_workspace(workspace_id)
    if not success or descriptor_obj is None:
        return _json_error(error or f"Workspace {workspace_id} is unavailable", workspace_id=workspace_id)
    descriptor = descriptor_obj

    try:
        _run_docker(["cp", str(host_path), f"{resources.container}:{target_path}"], check=True)
    except WorkspaceError as exc:
        return _json_error(str(exc), workspace_id=workspace_id)

    result = WorkspaceCopyResult(
        workspace=descriptor,
        source=str(host_path),
        destination=target_path,
        bytes_transferred=bytes_transferred,
    )
    _publish_workspace_event(
        workspace_id,
        "copy_to",
        {
            "source": str(host_path),
            "destination": target_path,
            "bytes_transferred": bytes_transferred,
        },
    )
    return json.dumps({"success": True, "workspace_copy": result.dict()}, indent=2)


@tool(args_schema=WorkspaceCopyFromRequest)
def workspace_copy_from(
    workspace_id: str,
    source_path: str,
    destination_path: str,
) -> str:
    """Copy a file or directory from the workspace volume to the host."""

    resources = _workspace_resources(workspace_id)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    success, descriptor_obj, error = ensure_workspace(workspace_id)
    if not success or descriptor_obj is None:
        return _json_error(error or f"Workspace {workspace_id} is unavailable", workspace_id=workspace_id)
    descriptor = descriptor_obj
    try:
        _run_docker(["cp", f"{resources.container}:{source_path}", str(destination)], check=True)
    except WorkspaceError as exc:
        return _json_error(str(exc), workspace_id=workspace_id)

    bytes_transferred: Optional[int] = None
    if destination.exists() and destination.is_file():
        bytes_transferred = destination.stat().st_size

    result = WorkspaceCopyResult(
        workspace=descriptor,
        source=source_path,
        destination=str(destination),
        bytes_transferred=bytes_transferred,
    )
    _publish_workspace_event(
        workspace_id,
        "copy_from",
        {
            "source": source_path,
            "destination": str(destination),
            "bytes_transferred": bytes_transferred,
        },
    )
    return json.dumps({"success": True, "workspace_copy": result.dict()}, indent=2)


@tool(args_schema=WorkspaceDestroyRequest)
def workspace_destroy(
    workspace_id: str,
    delete_volume: bool = True,
) -> str:
    """Stop the workspace container and remove associated volume."""

    resources = _workspace_resources(workspace_id)
    container_removed = False
    volume_removed = False
    errors: List[str] = []

    if _container_exists(resources.container):
        result = _run_docker(["container", "rm", "-f", resources.container])
        if result.returncode == 0:
            container_removed = True
        else:
            errors.append(result.stderr.strip() or f"Failed to remove container {resources.container}")

    if delete_volume and _volume_exists(resources.volume):
        result = _run_docker(["volume", "rm", resources.volume])
        if result.returncode == 0:
            volume_removed = True
        else:
            errors.append(result.stderr.strip() or f"Failed to remove volume {resources.volume}")

    payload = {
        "success": not errors,
        "workspace_id": workspace_id,
        "container_removed": container_removed,
        "volume_removed": volume_removed,
    }
    if errors:
        payload["errors"] = errors
    event_payload = {
        "container_removed": container_removed,
        "volume_removed": volume_removed,
    }
    if errors:
        event_payload["errors"] = errors
    _publish_workspace_event(workspace_id, "workspace_destroyed", event_payload)
    return json.dumps(payload, indent=2)


@tool(args_schema=WorkspaceInfoRequest)
def workspace_info(
    workspace_id: str,
    include_volume_usage: bool = False,
) -> str:
    """Return descriptor and container/volume status for a workspace."""

    resources = _workspace_resources(workspace_id)
    if not _container_exists(resources.container):
        return _json_error("workspace_not_found", workspace_id=workspace_id)

    try:
        descriptor = _workspace_descriptor(resources)
        container_data = _inspect_container(resources.container)
    except WorkspaceError as exc:
        return _json_error(str(exc), workspace_id=workspace_id)

    info: Dict[str, Any] = {
        "success": True,
        "workspace": descriptor.dict(),
        "container": {
            "container_id": container_data.get("Id"),
            "state": container_data.get("State"),
            "name": container_data.get("Name"),
        },
    }

    try:
        volume_data = _inspect_volume(resources.volume)
    except WorkspaceError:
        volume_data = None

    if volume_data:
        info["volume"] = {
            "name": volume_data.get("Name"),
            "mountpoint": volume_data.get("Mountpoint"),
            "driver": volume_data.get("Driver"),
            "created_at": volume_data.get("CreatedAt"),
        }

    if include_volume_usage and volume_data:
        usage = _estimate_volume_usage(resources.volume)
        if usage is not None:
            info.setdefault("volume", {})["usage_bytes"] = usage

    return json.dumps(info, indent=2)


def _estimate_volume_usage(volume: str) -> Optional[int]:
    temp_container_name = f"qc-ws-usage-{uuid.uuid4().hex[:8]}"
    command = [
        "run",
        "--rm",
        "--name",
        temp_container_name,
        "--mount",
        f"type=volume,source={volume},target=/workspace",
        "alpine",
        "du",
        "-sb",
        "/workspace",
    ]
    result = _run_docker(command)
    if result.returncode != 0:
        return None
    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        size_str = stdout.split()[0]
        return int(size_str)
    except (IndexError, ValueError):
        return None


# Stable tool names
workspace_create.name = "workspace_create"
workspace_exec.name = "workspace_exec"
workspace_copy_to.name = "workspace_copy_to"
workspace_copy_from.name = "workspace_copy_from"
workspace_destroy.name = "workspace_destroy"
workspace_info.name = "workspace_info"
