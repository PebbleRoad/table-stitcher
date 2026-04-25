#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
else
  PYTHON_BIN=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -m build --version >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done

  if [[ -z "$PYTHON_BIN" ]] && command -v pytest >/dev/null 2>&1; then
    pytest_shebang="$(head -n 1 "$(command -v pytest)" | sed 's/^#!//')"
    if [[ -x "$pytest_shebang" ]] && "$pytest_shebang" -m build --version >/dev/null 2>&1; then
      PYTHON_BIN="$pytest_shebang"
    fi
  fi

  if [[ -z "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
  fi
fi

cd "$ROOT"

if ! "$PYTHON_BIN" -m build --version >/dev/null 2>&1; then
  echo "python-build is required. Install it with: $PYTHON_BIN -m pip install build" >&2
  exit 1
fi

echo "==> Running unit tests"
pytest -q

echo "==> Rebuilding dist/"
rm -rf dist
if [[ "${RELEASE_GATE_ONLINE:-0}" == "1" ]]; then
  "$PYTHON_BIN" -m build
else
  "$PYTHON_BIN" -m build --no-isolation
fi

wheel_count="$(find dist -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')"
sdist_count="$(find dist -maxdepth 1 -name '*.tar.gz' | wc -l | tr -d ' ')"
if [[ "$wheel_count" != "1" || "$sdist_count" != "1" ]]; then
  echo "Expected exactly one wheel and one sdist in dist/; got ${wheel_count} wheels and ${sdist_count} sdists" >&2
  exit 1
fi

WHEEL="$(find dist -maxdepth 1 -name '*.whl' -print -quit)"
TMP_ENV="$(mktemp -d)"
trap 'rm -rf "$TMP_ENV"' EXIT

echo "==> Installing wheel into clean venv"
if [[ "${RELEASE_GATE_ONLINE:-0}" == "1" ]]; then
  "$PYTHON_BIN" -m venv "$TMP_ENV/venv"
else
  "$PYTHON_BIN" -m venv --system-site-packages "$TMP_ENV/venv"
fi
if [[ "${RELEASE_GATE_ONLINE:-0}" == "1" ]]; then
  "$TMP_ENV/venv/bin/python" -m pip install --upgrade pip >/dev/null
  "$TMP_ENV/venv/bin/python" -m pip install "$WHEEL" >/dev/null
else
  "$TMP_ENV/venv/bin/python" -m pip install --no-deps "$WHEEL" >/dev/null
fi

echo "==> Smoke-testing installed wheel outside checkout"
(
  cd "$TMP_ENV"
  "$TMP_ENV/venv/bin/python" - <<'PY'
import pathlib
import table_stitcher

package_path = pathlib.Path(table_stitcher.__file__).resolve()
assert "site-packages" in str(package_path), package_path
assert table_stitcher.__version__, "missing __version__"
assert callable(table_stitcher.stitch_tables)
print(f"installed {table_stitcher.__version__} from {package_path}")
PY
)

echo "==> Release gate passed"
