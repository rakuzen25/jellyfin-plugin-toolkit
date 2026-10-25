"""Render consumer triggers around immutable shared workflow implementations."""

import re


def render(
    repository: str, revision: str, catalog: str | None = None, *, version: str
) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Workflow generation requires an immutable toolkit revision")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Workflow version must identify the locked toolkit release")
    prefix = repository + "/.github/workflows/"
    validate = """name: Validate release candidate
on:
  pull_request:
    types: [opened, synchronize, reopened, edited]
  workflow_dispatch:
    inputs:
      full:
        description: Execute all checks without evidence reuse
        type: boolean
        default: true
permissions:
  contents: read
  actions: read
jobs:
  validation:
    name: ${{ github.event_name == 'pull_request' && github.event.action == 'edited' && github.event.changes.base == null && 'metadata-event' || 'validation' }}
    if: github.event_name != 'pull_request' || github.event.action != 'edited' || github.event.changes.base != null
    uses: PREFIXvalidate.yml@REVISION # TOOLKIT_TAG
    with:
      full: ${{ inputs.full || false }}
"""
    safety = """name: Changed-input full safety check
on:
  schedule:
    - cron: "17 7 * * *"
  workflow_dispatch:
permissions:
  contents: read
  actions: read
jobs:
  safety:
    uses: PREFIXvalidate.yml@REVISION # TOOLKIT_TAG
    with:
      ref: ${{ github.sha }}
      scheduled: ${{ github.event_name == 'schedule' }}
      full: ${{ github.event_name == 'workflow_dispatch' }}
"""
    release = """name: Build draft release artifacts
on:
  workflow_dispatch:
    inputs:
      tag:
        description: Existing draft release tag to build or retry
        required: true
        type: string
      validated_run_id:
        description: Successful validation run whose immutable artifacts should be attached
        type: string
permissions:
  contents: read
  actions: read
  id-token: write
  attestations: write
jobs:
  release:
    uses: PREFIXrelease.yml@REVISION # TOOLKIT_TAG
    with:
      tag: ${{ inputs.tag }}
      validated_run_id: ${{ inputs.validated_run_id }}
      app_client_id: ${{ vars.PLUGIN_APP_CLIENT_ID }}
    secrets:
      PLUGIN_APP_PRIVATE_KEY: ${{ secrets.PLUGIN_APP_PRIVATE_KEY }}
"""
    prepare = """name: Prepare release
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  actions: read
  id-token: write
  attestations: write
jobs:
  prepare:
    uses: PREFIXprepare-release.yml@REVISION # TOOLKIT_TAG
    with:
      app_client_id: ${{ vars.PLUGIN_APP_CLIENT_ID }}
    secrets:
      PLUGIN_APP_PRIVATE_KEY: ${{ secrets.PLUGIN_APP_PRIVATE_KEY }}
"""
    setup = """name: Set up connected GitHub App
on:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  configure:
    if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
    uses: PREFIXsetup-automation.yml@REVISION # TOOLKIT_TAG
    with:
      app_client_id: ${{ vars.PLUGIN_APP_CLIENT_ID }}
      retry_closed: true
    secrets:
      PLUGIN_APP_PRIVATE_KEY: ${{ secrets.PLUGIN_APP_PRIVATE_KEY }}
"""
    contents = {
        "app-setup.yml": setup,
        "validate.yml": validate,
        "safety.yml": safety,
        "release.yml": release,
        "release-please.yml": prepare,
        "pr-title.yml": """name: Check PR title
on:
  pull_request:
    types: [opened, synchronize, reopened, edited]
permissions:
  contents: read
jobs:
  title:
    uses: PREFIXpr-title.yml@REVISION # TOOLKIT_TAG
""",
    }
    if catalog:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", catalog):
            raise ValueError("Invalid catalog repository")
        contents["catalog-notify.yml"] = """name: Notify configured plugin catalog
on:
  release:
    types: [published]
  workflow_dispatch:
    inputs:
      tag:
        description: Published stable release tag to retry
        type: string
        required: true
permissions:
  contents: read
jobs:
  notify:
    if: github.event_name == 'workflow_dispatch' || (!github.event.release.draft && !github.event.release.prerelease)
    uses: PREFIXcatalog-notify.yml@REVISION # TOOLKIT_TAG
    with:
      catalog_repository: CATALOG
      tag: ${{ github.event.release.tag_name || inputs.tag }}
      app_client_id: ${{ vars.PLUGIN_APP_CLIENT_ID }}
    secrets:
      PLUGIN_APP_PRIVATE_KEY: ${{ secrets.PLUGIN_APP_PRIVATE_KEY }}
""".replace("CATALOG", catalog)
    return {
        name: text.replace("PREFIX", prefix)
        .replace("REVISION", revision)
        .replace("TOOLKIT_TAG", version)
        for name, text in contents.items()
    }


def render_sync(
    repository: str,
    revision: str,
    *,
    version: str,
    validation_workflow: str = "Validate release candidate",
) -> str:
    """Optional consumer-owned caller; synchronization only maintains its toolkit pin."""
    # Reuse the same validation as the standard workflow bindings.
    render(repository, revision, version=version)
    import json

    return (
        """name: Synchronize toolkit update
on:
  workflow_run:
    workflows: [VALIDATION]
    types: [completed]
  workflow_dispatch:
    inputs:
      pr_number:
        description: Renovate toolkit PR to synchronize
        type: string
        required: true
permissions:
  contents: read
  actions: read
  pull-requests: read
jobs:
  synchronize:
    uses: REPOSITORY/.github/workflows/sync-bindings.yml@REVISION # TOOLKIT_TAG
    with:
      run_id: ${{ github.event_name == 'workflow_run' && format('{0}', github.event.workflow_run.id) || '' }}
      pr_number: ${{ inputs.pr_number || '' }}
      app_client_id: ${{ vars.PLUGIN_APP_CLIENT_ID }}
    secrets:
      PLUGIN_APP_PRIVATE_KEY: ${{ secrets.PLUGIN_APP_PRIVATE_KEY }}
""".replace("VALIDATION", json.dumps(validation_workflow))
        .replace("REPOSITORY", repository)
        .replace("REVISION", revision)
        .replace("TOOLKIT_TAG", version)
    )
