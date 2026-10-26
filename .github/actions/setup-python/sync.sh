#!/usr/bin/env bash
set -euo pipefail
case "$JPT_SETUP_MODE" in
  development) uv sync --project "$JPT_PROJECT" --locked ;;
  runtime) uv sync --project "$JPT_PROJECT" --locked --no-dev ;;
  *) echo '::error::Expected development or runtime synchronization'; exit 1 ;;
esac
