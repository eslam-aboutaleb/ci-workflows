# Reusable CI workflows

Shared GitHub Actions workflows for Python, Node.js, browser tests, service integration, security analysis, and Python package releases.

## How to use

Create `.github/workflows/ci.yml` in your project and call the workflows you need as jobs:

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}

jobs:
  python:
    uses: OWNER/WORKFLOW_REPOSITORY/.github/workflows/python.yml@COMMIT_SHA
    with:
      project-dir: .
      manager: uv
      python-versions: '["3.11", "3.12"]'
      coverage-source: my_package
      coverage-min: 85
```

Replace `OWNER/WORKFLOW_REPOSITORY` with this repository's location and `COMMIT_SHA` with a reviewed 40-character commit SHA. Set paths, versions and thresholds for your project. For pip projects, use `manager: pip` and provide `pip-extras` and `pip-constraints` when needed.

GitHub calls this **reusing workflows**. Each call uses the referenced workflow revision and checks out your project's code. Updating this repository does not change SHA-pinned callers; update the SHA in your project to adopt a newer revision. A repository template does not provide ongoing inheritance.

Add your own jobs alongside these calls, or use `needs: python` to run a project-specific job after the shared Python workflow succeeds. Event triggers, concurrency and required branch checks belong in your project's caller workflow.

## What each workflow does

| Workflow | Checks and outputs | Required inputs |
| --- | --- | --- |
| [`python.yml`](.github/workflows/python.yml) | Ruff lint/format, pytest across Python versions, optional coverage threshold/report and wheel/sdist validation | `project-dir`, `manager` (`uv` or `pip`) |
| [`node.yml`](.github/workflows/node.yml) | Frozen npm/pnpm install, dependency audit, lint, typecheck, tests and build across Node versions | `project-dir`, `manager` (`npm` or `pnpm`) |
| [`browser.yml`](.github/workflows/browser.yml) | Install Chromium, run the project's Playwright script, retain its HTML report for 7 days | `project-dir`, `manager` |
| [`integration.yml`](.github/workflows/integration.yml) | Start PostgreSQL and optional Redis, install Python tooling, run project-owned installation, migration and integration targets | `project-dir` |
| [`security.yml`](.github/workflows/security.yml) | CodeQL analysis and dependency review for newly introduced vulnerabilities on pull requests | No required inputs |
| [`publish-pypi.yml`](.github/workflows/publish-pypi.yml) | Verify package name/version against the release tag, build and validate distributions, publish through OIDC | `project-dir`, `distribution-name` |
| [`validate.yml`](.github/workflows/validate.yml) | Validate this repository's workflow syntax and contracts with actionlint | Runs here automatically; not reusable |

## Configuration

Inputs and defaults are defined under `on.workflow_call.inputs` in each workflow.

- **Python:** `python-versions` is a JSON array. uv projects require `uv.lock` and locked test/Ruff dependencies. pip projects use an editable install; optional `pip-extras` defaults to empty and `pip-constraints` accepts a constraints file relative to `project-dir`. `lint` defaults to true. Set `coverage-source` and `coverage-min` to enable a coverage gate. Set `build-distribution: true` for package build checks.
- **Node:** provide a lockfile and `lint`, `test`, `build`, and `typecheck` scripts. `node-versions` is a JSON array. pnpm reads `packageManager` from the project's `package.json`, or accepts `pnpm-version`. Set `typecheck-script` to another script name or `''` to disable it; `run-tests: false` disables tests. `audit-level` defaults to `high`; `none` explicitly disables the full audit.
- **Browser:** `test-script` defaults to `test:e2e`. The project's Playwright configuration must start its application and test services. Snapshot updates must be reviewed in the project, not generated during CI.
- **Integration:** the project Makefile must define `ci-install`, `ci-migrate`, and `ci-integration`. These targets receive `CI_POSTGRES_URL` for a disposable `ci_test` database. `postgres-image` defaults to `postgres:16`; set it to a compatible image or immutable digest for extensions such as pgvector. `redis-image` defaults to empty (disabled); when configured, `CI_REDIS_URL` is provided. `install-uv` and `python-version` control Python tooling.
- **Security:** `languages` is a JSON array, defaulting to Python and JavaScript/TypeScript. The caller job must grant `contents: read`, `actions: read`, `security-events: write`, and `pull-requests: read`. CodeQL and dependency review require the corresponding GitHub security features to be available and enabled.
- **PyPI:** call from `release: types: [published]`. Only non-prereleases tagged `v<pyproject version>` publish. Grant the caller job `contents: read` and `id-token: write`; configure a protected `pypi` environment and PyPI Trusted Publishing for the caller and reusable workflow as required by PyPI. No PyPI API token is needed.

## Permissions and updates

Normal CI jobs use read-only repository access, do not receive secrets, and disable persisted checkout credentials. External Actions are pinned to full commit SHAs. Publishing has a separate job with OIDC permission and an environment approval boundary; configure its environment protection before enabling releases.

Keep domain-specific checks in your project. Verify new caller jobs alongside existing CI before making them required. Workflow syntax validation does not prove that a project's dependencies, scripts or service fixtures pass. Review workflow changes and update caller SHAs deliberately.
