#!/usr/bin/env bash
# Starts the self-host stack from an image and checks that it answers: health, the web app, a CSV upload and the
# MCP server. No model key is needed. Used by .github/workflows/release.yml before an image is published.
#
#   IMAGE=nl-analytics-agent VERSION=local self-host/smoke.sh
set -euo pipefail
cd "$(dirname "$0")"

export PORT="${PORT:-8000}" COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-sales-analyst-smoke}"
base="http://localhost:$PORT"
jar="$(mktemp)"
csv="$(mktemp --suffix .csv)"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then docker compose logs app || true; fi
  docker compose down -v >/dev/null 2>&1 || true
  rm -f "$jar" "$csv"
  exit "$status"
}
trap cleanup EXIT

check() {  # check "what" <command...>: runs the command and says which check failed
  local what="$1"; shift
  if ! "$@"; then echo "FAILED: $what" >&2; return 1; fi
  echo "ok: $what"
}

docker compose up -d --wait --wait-timeout 180

check "ready" sh -c "curl -fsS '$base/readyz' | grep -q '\"ok\"'"
check "sample data loaded" sh -c "curl -fsS '$base/v1/config' | grep -q 'Top 10 products'"
check "web app served" sh -c "curl -fsS '$base/' | grep -q '<div id=\"root\"'"
check "runs without root" sh -c "[ \"\$(docker compose exec -T app id -u)\" != 0 ]"

printf 'city,sales\nPune,10\nDelhi,20\n' > "$csv"
curl -fsS -c "$jar" -X POST "$base/v1/session" -H 'Content-Type: application/json' -d '{}' >/dev/null
check "CSV upload" sh -c "curl -fsS -b '$jar' -F 'file=@$csv' '$base/v1/datasets' | grep -q '\"rows\":2'"

check "MCP list_tables" sh -c "curl -fsS -X POST '$base/mcp' -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"list_tables\",\"arguments\":{}}}' \
  | grep -q order_items"
