#!/usr/bin/env bash
set -euo pipefail
message='GitHub App token acquisition failed. Inspect the original GitHub error above; verify PLUGIN_APP_CLIENT_ID and PLUGIN_APP_PRIVATE_KEY, the App installation on the requested repositories, and its requested permissions. Correct the credentials, installation, or permissions, then retry this operation. Running App setup does not repair these settings.'
echo "::error title=GitHub App authentication::$message"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '### GitHub App authentication\n\n%s\n' "$message" >> "$GITHUB_STEP_SUMMARY"
fi
exit 1
