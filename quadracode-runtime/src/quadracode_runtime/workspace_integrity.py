"""Workspace snapshotting, validation, and restoration utilities."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

from quadracode_contracts import WorkspaceSnapshotRecord

from .state import ExhaustionMode, QuadraCodeState

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WorkspaceIntegrityError(RuntimeError):
    """Raised when workspace integrity operations fail."""


@dataclass(slots=True)
class WorkspaceValidationResult:
    """Result payload for workspace validation attempts."""

    valid: bool
    actual_checksum: Optional[str]
    expected_checksum: Optional[str]
    restored: bool = False
    error: Optional[str] = None
    timestamp: str = _now_iso()


class WorkspaceIntegrityManager:
    """Coordinates workspace snapshotting, diffing, and restoration."""

    SNAPSHOT_LIMIT = 5

    def __init__(
        self,
        *,
        snapshot_root: str | Path | None = None,
        docker_bin: Optional[str] = None,
        snapshot_image: Optional[str] = None,
    ) -> None:
        root = snapshot_root or os.environ.get(
            "QUADRACODE_WORKSPACE_SNAPSHOT_ROOT",
            os.path.join(tempfile.gettempdir(), "quadracode", "workspace_snapshots"),
        )
        self.snapshot_root = Path(root).expanduser().resolve()
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        self.docker_bin = docker_bin or os.environ.get("QUADRACODE_DOCKER_BIN", "docker")
        self.snapshot_image = snapshot_image or os.environ.get(
            "QUADRACODE_WORKSPACE_SNAPSHOT_IMAGE",
            "alpine:3.19",
        )
        self._lock = Lock()

    def capture_snapshot(
        self,
        *,
        descriptor: Mapping[str, Any],
        reason: str,
        exhaustion_mode: ExhaustionMode | str | None = None,
        metadata: Optional[Dict[str, Any]] = None,
        previous_snapshot: WorkspaceSnapshotRecord | None = None,
    ) -> WorkspaceSnapshotRecord:
        """Capture a persistent snapshot and optional diff for the workspace."""

        with self._lock:
            workspace_id = self._coerce_workspace_id(descriptor)
            workspace_dir = self._prepare_workspace_dir(workspace_id)
            prefix = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
            archive_path, manifest, checksum = self._collect_workspace_state(
                descriptor,
                workspace_dir=workspace_dir,
                prefix=prefix,
                persist_archive=True,
            )
            manifest_path = workspace_dir / f"{prefix}-manifest.json"
            self._write_manifest(manifest_path, manifest)
            diff_path = None
            if previous_snapshot:
                try:
                    previous_manifest = self._read_manifest(Path(previous_snapshot.manifest_path))
                    diff_text = self._generate_manifest_diff(previous_manifest, manifest)
                except FileNotFoundError:
                    diff_text = ""
                if diff_text.strip():
                    diff_path_obj = workspace_dir / f"{prefix}-diff.patch"
                    diff_path_obj.write_text(diff_text, encoding="utf-8")
                    diff_path = diff_path_obj
            snapshot = WorkspaceSnapshotRecord(
                snapshot_id=f"{workspace_id}-{prefix}",
                workspace_id=workspace_id,
                created_at=_now_iso(),
                reason=reason,
                checksum=checksum,
                manifest_path=str(manifest_path),
                archive_path=str(archive_path),
                diff_path=str(diff_path) if diff_path else None,
                exhaustion_mode=(
                    exhaustion_mode.value
                    if isinstance(exhaustion_mode, ExhaustionMode)
                    else exhaustion_mode
                ),
                metadata=metadata or {},
            )
            return snapshot

    def validate_workspace(
        self,
        *,
        descriptor: Mapping[str, Any],
        reference: WorkspaceSnapshotRecord,
        auto_restore: bool = False,
    ) -> WorkspaceValidationResult:
        """Validate current workspace checksum against a reference snapshot."""

        with self._lock:
            workspace_dir = self._prepare_workspace_dir(reference.workspace_id)
            _, manifest, checksum = self._collect_workspace_state(
                descriptor,
                workspace_dir=workspace_dir,
                prefix=f"validation-{uuid.uuid4().hex[:6]}",
                persist_archive=False,
            )
            valid = checksum == reference.checksum
            restored = False
            error: Optional[str] = None
            if not valid and auto_restore:
                try:
                    self._restore_from_snapshot(descriptor, reference)
                    restored = True
                except WorkspaceIntegrityError as exc:  # pragma: no cover - difficult to induce
                    error = str(exc)
            return WorkspaceValidationResult(
                valid=valid,
                actual_checksum=checksum,
                expected_checksum=reference.checksum,
                restored=restored,
                error=error,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare_workspace_dir(self, workspace_id: str) -> Path:
        path = self.snapshot_root / workspace_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _coerce_workspace_id(self, descriptor: Mapping[str, Any]) -> str:
        workspace_id = descriptor.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise WorkspaceIntegrityError("workspace descriptor missing workspace_id")
        return workspace_id.strip()

    def _collect_workspace_state(
        self,
        descriptor: Mapping[str, Any],
        *,
        workspace_dir: Path,
        prefix: str,
        persist_archive: bool,
    ) -> tuple[Path, List[Dict[str, Any]], str]:
        if persist_archive:
            archive_path = workspace_dir / f"{prefix}.tar.gz"
        else:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz")
            os.close(tmp_fd)
            archive_path = Path(tmp_path)

        try:
            self._export_workspace(descriptor, archive_path)
            manifest = self._manifest_from_archive(archive_path)
            checksum = self._aggregate_checksum(manifest)
        finally:
            if not persist_archive:
                try:
                    archive_path.unlink()
                except FileNotFoundError:
                    pass
        if not persist_archive:
            # ensure callers don't rely on deleted archives
            archive_path = workspace_dir / f"{prefix}-transient"
        return archive_path, manifest, checksum

    def _export_workspace(self, descriptor: Mapping[str, Any], archive_path: Path) -> None:
        host_path = descriptor.get("host_path")
        if isinstance(host_path, str) and host_path.strip():
            self._archive_host_path(Path(host_path.strip()), archive_path)
            return
        volume = descriptor.get("volume")
        if not isinstance(volume, str) or not volume.strip():
            raise WorkspaceIntegrityError("workspace descriptor missing volume")
        self._archive_volume(volume.strip(), archive_path)

    def _archive_host_path(self, root: Path, archive_path: Path) -> None:
        if not root.exists():
            raise WorkspaceIntegrityError(f"host workspace path {root} does not exist")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(root, arcname=".")

    def _archive_volume(self, volume: str, archive_path: Path) -> None:
        command = [
            "run",
            "--rm",
            "--mount",
            f"type=volume,source={volume},target=/snapshot",
            self.snapshot_image,
            "sh",
            "-c",
            "cd /snapshot && tar czf - .",
        ]
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("wb") as stdout:
            result = self._run_docker(command, stdout=stdout)
        if result.returncode != 0:
            raise WorkspaceIntegrityError(
                f"Failed to archive workspace volume {volume}: {result.stderr.decode().strip()}"
            )

    def _manifest_from_archive(self, archive_path: Path) -> List[Dict[str, Any]]:
        manifest: List[Dict[str, Any]] = []
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                digest = hashlib.sha256()
                while True:
                    chunk = extracted.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                manifest.append(
                    {
                        "path": member.name,
                        "size": member.size,
                        "sha256": digest.hexdigest(),
                    }
                )
        manifest.sort(key=lambda item: item["path"])
        return manifest

    def _aggregate_checksum(self, manifest: Iterable[Dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        for entry in manifest:
            digest.update(entry.get("path", "").encode("utf-8"))
            digest.update(str(entry.get("size", 0)).encode("utf-8"))
            digest.update(entry.get("sha256", "").encode("utf-8"))
        return digest.hexdigest()

    def _write_manifest(self, manifest_path: Path, manifest: List[Dict[str, Any]]) -> None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _read_manifest(self, manifest_path: Path) -> List[Dict[str, Any]]:
        text = manifest_path.read_text(encoding="utf-8")
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise ValueError("manifest payload malformed")
        return [dict(entry) for entry in payload]

    def _generate_manifest_diff(
        self,
        previous: Iterable[Mapping[str, Any]],
        current: Iterable[Mapping[str, Any]],
    ) -> str:
        def _to_lines(entries: Iterable[Mapping[str, Any]]) -> List[str]:
            lines: List[str] = []
            for entry in entries:
                lines.append(
                    f"{entry.get('path','')}|{entry.get('size',0)}|{entry.get('sha256','')}"
                )
            return lines

        previous_lines = _to_lines(previous)
        current_lines = _to_lines(current)
        diff = difflib.unified_diff(
            previous_lines,
            current_lines,
            fromfile="previous",
            tofile="current",
            lineterm="",
        )
        return "\n".join(diff)

    def _restore_from_snapshot(
        self,
        descriptor: Mapping[str, Any],
        snapshot: WorkspaceSnapshotRecord,
    ) -> None:
        archive_path = Path(snapshot.archive_path)
        if not archive_path.exists():
            raise WorkspaceIntegrityError(f"snapshot archive missing: {archive_path}")
        host_path = descriptor.get("host_path")
        if isinstance(host_path, str) and host_path.strip():
            self._restore_host_path(Path(host_path.strip()), archive_path)
            return
        volume = descriptor.get("volume")
        if not isinstance(volume, str) or not volume.strip():
            raise WorkspaceIntegrityError("workspace descriptor missing volume for restore")
        self._restore_volume(volume.strip(), archive_path)

    def _restore_host_path(self, root: Path, archive_path: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._clear_directory(root)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=root, filter="data")

    def _restore_volume(self, volume: str, archive_path: Path) -> None:
        command = [
            "run",
            "--rm",
            "--mount",
            f"type=volume,source={volume},target=/restore",
            "-i",
            self.snapshot_image,
            "sh",
            "-c",
            "cd /restore && find . -mindepth 1 -delete && tar xzf -",
        ]
        with archive_path.open("rb") as stdin:
            result = self._run_docker(command, stdin=stdin)
        if result.returncode != 0:
            raise WorkspaceIntegrityError(
                f"Failed to restore workspace volume {volume}: {result.stderr.decode().strip()}"
            )

    def _clear_directory(self, root: Path) -> None:
        for entry in list(root.iterdir()):
            try:
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except FileNotFoundError:
                continue

    def _run_docker(
        self,
        args: List[str],
        *,
        stdout: Optional[Any] = None,
        stdin: Optional[Any] = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [self.docker_bin, *args]
        try:
            result = subprocess.run(
                command,
                stdout=stdout or subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=stdin,
                check=False,
            )
            return result
        except FileNotFoundError as exc:  # pragma: no cover - depends on env
            raise WorkspaceIntegrityError(f"Docker binary not found at '{self.docker_bin}'") from exc


_MANAGER: WorkspaceIntegrityManager | None = None
_MANAGER_LOCK = Lock()


def get_workspace_integrity_manager() -> WorkspaceIntegrityManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = WorkspaceIntegrityManager()
    return _MANAGER


def _last_snapshot(state: QuadraCodeState) -> WorkspaceSnapshotRecord | None:
    snapshots = state.get("workspace_snapshots") or []
    for entry in reversed(snapshots):
        if isinstance(entry, WorkspaceSnapshotRecord):
            return entry
        if isinstance(entry, MutableMapping):
            try:
                return WorkspaceSnapshotRecord(**entry)
            except Exception:  # noqa: BLE001
                continue
    return None


def capture_workspace_snapshot(
    state: QuadraCodeState,
    *,
    reason: str,
    stage: Optional[str] = None,
    exhaustion_mode: Optional[ExhaustionMode] = None,
    metadata: Optional[Dict[str, Any]] = None,
    max_snapshots: int | None = None,
) -> WorkspaceSnapshotRecord | None:
    descriptor = state.get("workspace")
    if not isinstance(descriptor, Mapping):
        return None
    manager = get_workspace_integrity_manager()
    payload_metadata = dict(metadata or {})
    if stage:
        payload_metadata.setdefault("stage", stage)
    previous = _last_snapshot(state)
    try:
        snapshot = manager.capture_snapshot(
            descriptor=descriptor,
            reason=reason,
            exhaustion_mode=exhaustion_mode,
            metadata=payload_metadata,
            previous_snapshot=previous,
        )
    except WorkspaceIntegrityError as exc:
        logger.warning("workspace snapshot failed: %s", exc)
        _record_workspace_metric(
            state,
            "workspace_snapshot_failed",
            {
                "reason": reason,
                "stage": stage,
                "error": str(exc),
            },
        )
        return None

    snapshots = state.setdefault("workspace_snapshots", [])
    snapshots.append(snapshot)
    limit = max_snapshots or WorkspaceIntegrityManager.SNAPSHOT_LIMIT
    if limit > 0 and len(snapshots) > limit:
        del snapshots[:-limit]
    _record_workspace_metric(
        state,
        "workspace_snapshot",
        {
            "snapshot_id": snapshot.snapshot_id,
            "reason": reason,
            "stage": stage,
            "exhaustion_mode": snapshot.exhaustion_mode,
            "archive_path": snapshot.archive_path,
        },
    )
    return snapshot


def validate_workspace_integrity(
    state: QuadraCodeState,
    *,
    reason: str,
    auto_restore: bool = False,
) -> WorkspaceValidationResult | None:
    descriptor = state.get("workspace")
    if not isinstance(descriptor, Mapping):
        return None
    reference = _last_snapshot(state)
    if reference is None:
        return None
    manager = get_workspace_integrity_manager()
    try:
        result = manager.validate_workspace(
            descriptor=descriptor,
            reference=reference,
            auto_restore=auto_restore,
        )
    except WorkspaceIntegrityError as exc:
        logger.warning("workspace validation failed: %s", exc)
        _update_validation_state(
            state,
            status="error",
            checksum=None,
            error=str(exc),
        )
        _record_workspace_metric(
            state,
            "workspace_validation_failed",
            {
                "reason": reason,
                "error": str(exc),
            },
        )
        return None

    status = "clean" if result.valid else ("restored" if result.restored else "drift_detected")
    _update_validation_state(
        state,
        status=status,
        checksum=result.actual_checksum or reference.checksum,
        error=result.error,
    )
    payload = {
        "reason": reason,
        "status": status,
        "valid": result.valid,
        "restored": result.restored,
        "expected_checksum": result.expected_checksum,
        "actual_checksum": result.actual_checksum,
    }
    if result.error:
        payload["error"] = result.error
    _record_workspace_metric(state, "workspace_validation", payload)
    return result


def _update_validation_state(
    state: QuadraCodeState,
    *,
    status: str,
    checksum: Optional[str],
    error: Optional[str],
) -> None:
    validation = state.setdefault("workspace_validation", {})
    validation.update(
        {
            "status": status,
            "last_checksum": checksum,
            "validated_at": _now_iso(),
            "last_error": error,
        }
    )
    if status in {"drift_detected", "error"}:
        validation["failure_count"] = int(validation.get("failure_count", 0) or 0) + 1


def _record_workspace_metric(
    state: QuadraCodeState,
    event: str,
    payload: Dict[str, Any],
) -> None:
    metrics = state.setdefault("metrics_log", [])
    metrics.append(
        {
            "event": event,
            "payload": payload,
            "timestamp": _now_iso(),
        }
    )
