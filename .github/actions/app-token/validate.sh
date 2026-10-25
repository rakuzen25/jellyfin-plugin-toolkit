#!/usr/bin/env bash
set -euo pipefail

fail() {
    echo "::error title=GitHub App configuration::$1"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
        printf '### GitHub App configuration\n\n%s\n' "$1" >> "$GITHUB_STEP_SUMMARY"
    fi
    exit 1
}

if [[ -z "${APP_CLIENT_ID//[[:space:]]/}" || -z "${APP_PRIVATE_KEY//[[:space:]]/}" ]]; then
    fail 'Configure Actions variable PLUGIN_APP_CLIENT_ID and secret PLUGIN_APP_PRIVATE_KEY for the connected App, then retry this operation. App setup cannot repair credentials.'
fi
if [[ -n "${APP_OWNER//[[:space:]]/}" && -z "${APP_REPOSITORIES//[[:space:],]/}" ]]; then
    fail 'An explicit installation owner requires an explicit repository list; refusing an installation-wide token.'
fi
selected=false
for name in CONTENTS PULL_REQUESTS ISSUES ACTIONS ATTESTATIONS WORKFLOWS; do
    variable="APP_PERMISSION_$name"
    value="${!variable:-}"
    if [[ -z "$value" ]]; then continue; fi
    if [[ "$value" != read && "$value" != write ]] || [[ "$name" == WORKFLOWS && "$value" != write ]]; then
        fail 'Token permissions must be read or write; workflows permission supports write only.'
    fi
    selected=true
done
if [[ "$selected" != true ]]; then
    fail 'Specify the permissions required by this operation; refusing a token with all installation permissions.'
fi
