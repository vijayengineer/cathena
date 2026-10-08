#!/usr/bin/env bash
# Build dist/cathena-<date>.tar.gz: everything the server needs, nothing secret.
# Explicit file list: .env, data/ (users, keys, secrets) and video sources are never included.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pytest -q
out="dist/cathena-$(date +%Y%m%d-%H%M).tar.gz"
mkdir -p dist
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' -czf "$out" \
  app web media tests docs scripts deploy \
  pyproject.toml uv.lock .python-version \
  Dockerfile .dockerignore docker-compose.yml Caddyfile .env.example README.md CLAUDE.md
if tar -tzf "$out" | grep -E '(^|/)\.env$|(^|/)data/|agent_key_secret|session_secret|users\.json' ; then
  echo "refusing: secret or state file in package" >&2; rm -f "$out"; exit 1
fi
echo "$out  $(du -h "$out" | cut -f1)"
