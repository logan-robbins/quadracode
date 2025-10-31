#!/usr/bin/env bash
set -euo pipefail

# Stream all Quadracode mailboxes (qc:mailbox/*) live, nicely formatted.
# Usage: bash scripts/tail_streams.sh

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found; please install Docker" >&2
  exit 1
fi

# Discover all mailbox streams
readarray -t STREAMS < <(docker compose exec -T redis \
  redis-cli --raw KEYS 'qc:mailbox/*' | tr '\r' '\n' | sed '/^$/d' | sort)

if [ ${#STREAMS[@]} -eq 0 ]; then
  echo "No mailboxes found (pattern qc:mailbox/*)." >&2
  exit 0
fi

printf "Streaming %d mailbox(es):\n" "${#STREAMS[@]}" >&2
for s in "${STREAMS[@]}"; do
  echo " - $s" >&2
done

# Build redis-cli XREAD args: STREAMS <keys...> <$...>
ARGS=(XREAD BLOCK 0 STREAMS)
for s in "${STREAMS[@]}"; do ARGS+=("$s"); done
for _ in "${STREAMS[@]}"; do ARGS+=("$"); done

# Pretty streaming output with jq and basic ANSI colors
docker compose exec -T redis redis-cli --json "${ARGS[@]}" | jq -r '
  def to_obj: reduce range(0; length; 2) as $i ({}; . + { (.[ $i ]) : .[$i+1] });
  def c($n; $s): "\u001b[" + ($n|tostring) + "m" + $s + "\u001b[0m";
  def sc($stream): if ($stream|contains("/human")) then 35 elif ($stream|contains("/orchestrator")) then 34 else 32 end;
  .[] as $s
  | $s[0] as $stream
  | ($s[1] // [])[] as $e
  | $e[0] as $id
  | ($e[1] // [] | to_obj) as $o
  | ( $o.timestamp // now | todateiso8601 ) as $ts
  | ( $o.message // "" ) as $msg
  | ( $o.sender // "?" ) as $snd
  | ( $o.recipient // "?" ) as $rcp
  | "[" + $ts + "] "
    + c(sc($stream); $stream)
    + " " + c(90; $id)
    + " " + c(36; $snd) + " -> " + c(33; $rcp)
    + ": " + $msg
'
