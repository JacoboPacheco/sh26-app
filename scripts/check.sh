#!/usr/bin/env bash
# One command that proves the app works: frontend lint + build, the backend smoke
# test against a real running server, then a headless-browser check of the built
# app (renders, no crash, no console errors, alt text + labels, fits a phone).
# Exits non-zero on any failure. Used by Claude Code (/check), by CI, and by you.
#
# Usage (from repo root, Git Bash or any bash): bash scripts/check.sh

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8765          # throwaway backend
PREVIEW_PORT=4173  # throwaway frontend (vite preview of the build)
failed=()

if [ -x "$ROOT/backend/venv/Scripts/python.exe" ]; then
  PY="$ROOT/backend/venv/Scripts/python.exe"
elif [ -x "$ROOT/backend/venv/bin/python" ]; then
  PY="$ROOT/backend/venv/bin/python"
else
  PY="python"
fi

kill_port() {
  if command -v taskkill >/dev/null 2>&1; then
    for pid in $(netstat -ano 2>/dev/null | grep LISTENING | grep ":$1 " | awk '{print $5}' | sort -u); do
      taskkill //F //PID "$pid" >/dev/null 2>&1
    done
  elif command -v lsof >/dev/null 2>&1; then
    lsof -ti :"$1" 2>/dev/null | xargs -r kill 2>/dev/null
  fi
}

# the interpreter that has Playwright: the backend venv if it's there, else the system python
E2E_PY=""
for candidate in "$PY" python python3; do
  if "$candidate" -c "import playwright.sync_api" >/dev/null 2>&1; then E2E_PY="$candidate"; break; fi
done

cleanup() {
  kill_port "$PORT"; kill_port "$PREVIEW_PORT"
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null
  [ -n "${PREVIEW_PID:-}" ] && kill "$PREVIEW_PID" 2>/dev/null
  rm -f "$ROOT/backend/check.db"
}
trap cleanup EXIT

echo "== frontend lint =="
(cd "$ROOT/frontend" && npm run lint --silent) || failed+=("frontend lint")

echo "== frontend build =="
# built against the throwaway backend so the browser check below exercises real requests
(cd "$ROOT/frontend" && VITE_API_URL="http://localhost:$PORT" npm run build --silent >/dev/null) && echo "build ok" || failed+=("frontend build")

echo "== backend smoke test (live server on :$PORT) =="
# :$PORT is reserved for this script; anything answering there is a leftover from a
# crashed run and would serve stale code, so replace it
if curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
  echo "(stopping a leftover check server on :$PORT)"
  kill_port "$PORT"
fi
rm -f "$ROOT/backend/check.db"
# GEMINI_API_KEY is blanked so the check never spends real quota or depends on the
# network: it verifies the deterministic "not configured" path instead.
# exec so SERVER_PID is the server itself, not a wrapper subshell
(cd "$ROOT/backend" && JWT_SECRET="${JWT_SECRET:-check-only-secret}" DATABASE_URL="sqlite:///./check.db" GEMINI_API_KEY="" \
  ALLOWED_ORIGINS="http://localhost:$PREVIEW_PORT" LOG_FILE="check-app.log" \
  exec "$PY" -m uvicorn main:app --port "$PORT" > "$ROOT/backend/check-server.log" 2>&1) &
SERVER_PID=$!

up=0
for _ in $(seq 1 20); do
  if curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1; then up=1; break; fi
  sleep 0.5
done

if [ "$up" -eq 1 ]; then
  (cd "$ROOT/backend" && SMOKE_BASE_URL="http://localhost:$PORT" "$PY" smoke_test.py) || failed+=("backend smoke test")
  # the built app may auto-login as the demo account (VITE_DEMO_EMAIL); make it exist
  (cd "$ROOT/backend" && "$PY" seed.py "http://localhost:$PORT" >/dev/null) || failed+=("seed demo account")
else
  echo "backend never came up — last server output:"
  tail -20 "$ROOT/backend/check-server.log"
  failed+=("backend startup")
fi

echo "== browser check (built app on :$PREVIEW_PORT against :$PORT) =="
skipped_browser=0
if [ "$up" -eq 1 ] && [ -n "$E2E_PY" ]; then
  kill_port "$PREVIEW_PORT"
  (cd "$ROOT/frontend" && exec node node_modules/vite/bin/vite.js preview --port "$PREVIEW_PORT" --strictPort > "$ROOT/frontend/check-preview.log" 2>&1) &
  PREVIEW_PID=$!
  pup=0
  for _ in $(seq 1 20); do
    if curl -sf "http://localhost:$PREVIEW_PORT/" >/dev/null 2>&1; then pup=1; break; fi
    sleep 0.5
  done
  if [ "$pup" -eq 1 ]; then
    "$E2E_PY" "$ROOT/frontend/e2e/smoke.py" "http://localhost:$PREVIEW_PORT" || failed+=("browser check")
    # the project's own demo path (written at milestone 1 from SPEC.md's demo script)
    if [ -f "$ROOT/frontend/e2e/demo_path.py" ]; then
      echo "== demo path (frontend/e2e/demo_path.py) =="
      "$E2E_PY" "$ROOT/frontend/e2e/demo_path.py" "http://localhost:$PREVIEW_PORT" || failed+=("demo path")
    fi
  else
    echo "vite preview never came up:"; tail -10 "$ROOT/frontend/check-preview.log"; failed+=("browser check")
  fi
elif [ "$up" -eq 1 ]; then
  skipped_browser=1
  echo "(SKIPPED: needs Python Playwright — python -m pip install playwright && python -m playwright install chromium)"
  [ -n "${CI:-}" ] && failed+=("browser check (Playwright missing in CI)")
  # Once the demo path exists, a skipped browser stage would hide a broken demo — that's a failure, not a skip.
  [ -f "$ROOT/frontend/e2e/demo_path.py" ] && failed+=("demo path (frontend/e2e/demo_path.py exists but Playwright is missing)")
fi

echo
if [ ${#failed[@]} -eq 0 ]; then
  if [ "$skipped_browser" -eq 1 ]; then echo "ALL CHECKS PASSED (browser check skipped — install Playwright)"; else echo "ALL CHECKS PASSED"; fi
  exit 0
fi
echo "FAILED: ${failed[*]}"
exit 1
