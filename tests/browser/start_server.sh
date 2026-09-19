#!/usr/bin/env bash
set -euo pipefail

python_bin="${PYTHON:-python}"
port="${PLUSONE_E2E_PORT:-8765}"
task_e2e_dir=""

if [[ -n "${PLUSONE_E2E_DATABASE_URL:-}" ]]; then
  "$python_bin" -c 'import sys; from urllib.parse import urlparse; u=urlparse(sys.argv[1]); db=u.path.rsplit("/", 1)[-1]; ok=u.scheme in {"postgres", "postgresql"} and u.hostname in {"localhost", "127.0.0.1"} and (db.endswith("_ci") or db.endswith("_e2e")); raise SystemExit(0 if ok else "Refusing non-local or non-test PostgreSQL URL")' "$PLUSONE_E2E_DATABASE_URL"
  export DATABASE_URL="$PLUSONE_E2E_DATABASE_URL"
else
  task_e2e_dir="$(mktemp -d "${TMPDIR:-/tmp}/plusone-e2e.XXXXXX")"
  export DATABASE_URL="sqlite:///$task_e2e_dir/newtest.sqlite3"
fi

cleanup() {
  if [[ -n "$task_e2e_dir" && -d "$task_e2e_dir" ]]; then
    rm -rf -- "$task_e2e_dir"
  fi
}
trap cleanup EXIT

"$python_bin" manage.py migrate --noinput
"$python_bin" manage.py runserver "127.0.0.1:$port" --noreload
