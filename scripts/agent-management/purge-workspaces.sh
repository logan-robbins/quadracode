#!/usr/bin/env bash
# Purge stale Quadracode workspace containers and volumes.
# Default behaviour removes workspace containers that are not running
# and older than the configured max age (in hours). Matching volumes
# are removed once their container is deleted.
#
# Usage:
#   purge-workspaces.sh [--max-age-hours HOURS] [--force] [--dry-run]
#
# Flags:
#   --max-age-hours HOURS  Maximum age threshold in hours (default: 24)
#   --force                Remove containers even if they are running
#   --dry-run              Print actions without removing resources

set -euo pipefail

MAX_AGE_HOURS=24
FORCE=0
DRY_RUN=0

print_usage() {
    sed -n '2,16p' "$0"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-age-hours)
            shift
            if [[ $# -eq 0 ]]; then
                echo "error: --max-age-hours requires a value" >&2
                exit 1
            fi
            MAX_AGE_HOURS="$1"
            ;;
        --force)
            FORCE=1
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        --help|-h)
            print_usage
            exit 0
            ;;
        *)
            echo "error: unknown argument '$1'" >&2
            print_usage
            exit 1
            ;;
    esac
    shift
done

if ! command -v docker >/dev/null 2>&1; then
    echo "error: docker CLI not found on PATH" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required to compute container age" >&2
    exit 1
fi

workspace_containers=()
while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    workspace_containers+=("$name")
done < <(docker ps -a --filter "name=qc-ws-" --format '{{.Names}}')

if [[ ${#workspace_containers[@]} -eq 0 ]]; then
    echo "No workspace containers found."
    exit 0
fi

cutoff_seconds=$(python3 - "$MAX_AGE_HOURS" <<'PY'
from datetime import datetime, timedelta, timezone
import sys
hours = float(sys.argv[1])
cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
print(f"{cutoff.timestamp():.0f}")
PY
) || {
    echo "error: failed to compute cutoff timestamp" >&2
    exit 1
}

removed_containers=()
removed_volumes=()
skipped_containers=()

for container in "${workspace_containers[@]}"; do
    if [[ ! "$container" =~ ^qc-ws-.*-ctr$ ]]; then
        continue
    fi

    created=$(docker inspect -f '{{.Created}}' "$container" 2>/dev/null || true)
    if [[ -z "$created" ]]; then
        echo "warning: unable to inspect container $container" >&2
        skipped_containers+=("$container")
        continue
    fi

    created_ts=$(python3 - "$created" <<'PY'
from datetime import datetime, timezone
import sys
created_value = sys.argv[1].strip()
if not created_value:
    raise SystemExit(0)
created_value = created_value.replace('Z', '+00:00')
try:
    created_dt = datetime.fromisoformat(created_value)
except ValueError:
    print()
else:
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    print(f"{created_dt.timestamp():.0f}")
PY
) || created_ts=""

    if [[ -z "$created_ts" ]]; then
        echo "warning: could not parse creation time for $container" >&2
        skipped_containers+=("$container")
        continue
    fi

    running_state=$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || echo "false")
    status=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo "unknown")

    # Skip if container is running and not forcing removal
    if [[ "$running_state" == "true" && "$FORCE" -ne 1 ]]; then
        skipped_containers+=("$container (running)")
        continue
    fi

    if (( created_ts > cutoff_seconds )) && [[ "$FORCE" -ne 1 ]]; then
        skipped_containers+=("$container (younger than threshold)")
        continue
    fi

    action="Removing container ${container} (status: ${status})"
    echo "$action"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        continue
    fi

    if ! docker rm -f "$container" >/dev/null; then
        echo "warning: failed to remove container $container" >&2
        skipped_containers+=("$container (remove failed)")
        continue
    fi

    removed_containers+=("$container")

    volume="${container%-ctr}"
    if [[ -n "$volume" ]]; then
        if docker volume inspect "$volume" >/dev/null 2>&1; then
            if docker ps -a --filter "volume=$volume" --format '{{.ID}}' | grep -q '.'; then
                continue
            fi
            echo "  Removing volume ${volume}"
            if docker volume rm "$volume" >/dev/null 2>&1; then
                removed_volumes+=("$volume")
            else
                echo "warning: failed to remove volume $volume" >&2
            fi
        fi
    fi
done

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Dry run completed. No containers or volumes were removed."
    exit 0
fi

echo
echo "Summary:"
echo "  Removed containers: ${#removed_containers[@]}"
echo "  Removed volumes: ${#removed_volumes[@]}"
if [[ ${#skipped_containers[@]} -gt 0 ]]; then
    echo "  Skipped:"
    for entry in "${skipped_containers[@]}"; do
        echo "    - $entry"
    done
fi
