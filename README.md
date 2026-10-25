# Shared CI workflows

**Documentation:** [Architecture and folders](docs/architecture.md) ·
[Shared image and infrastructure operations](docs/shared-operations.md) ·
[Gateway migration](docs/gateway-migration.md) · [Consumer examples](examples/)

A central GitHub Actions quality pipeline for repositories with Python, Node.js, browser tests, Docker images, or project-owned Makefile checks. Consuming repositories pin one workflow revision; discovery selects the matching checks, and **Quality gate** fails if any required check fails, is cancelled, or is unexpectedly skipped.

## Adopt in a repository

Copy [the caller example](.github/examples/caller.yml) to `.github/workflows/ci.yml` in your repository. Replace `COMMIT_SHA` with a reviewed full 40-character commit SHA from this repository and change `main` if your default branch has another name.

```yaml
name: CI
on:
  pull_request:
  merge_group:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  quality:
    uses: eslam-aboutaleb/ci-workflows/.github/workflows/ci.yml@COMMIT_SHA
    permissions:
      contents: read
      actions: read
      security-events: write
      pull-requests: read
    secrets:
      AI_EVAL_API_KEY: ${{ secrets.AI_EVAL_API_KEY }}
      PYPI_API_TOKEN: ${{ secrets.PYPI_API_TOKEN }}
```

Both keys are optional unless the matching feature is enabled. The PyPI token is required for enabled release publication. The provider key is optional. It is forwarded only to AI evaluations on default-branch pushes and manual runs against the default branch. Pull requests receive no provider key or publishing credentials. The aggregate caller needs the permissions shown because nested security and optional PyPI jobs declare those scopes; individual jobs narrow their token permissions. Container publishing is a separate opt-in call with `packages: write`.

After the first run, require the emitted **quality / Quality gate** check in your branch ruleset; use the exact name shown in GitHub. Enable required reviews, require code owner review of workflow and CI configuration changes, and include `merge_group` in callers that use a merge queue. Configure a code scanning ruleset to block the vulnerability severities you consider unacceptable: a successful CodeQL upload alone does not mean there are no findings.

Caller triggers belong to the consuming repository. The complete example also includes a weekly schedule and published releases. The central repository's triggers do not install triggers in other repositories. SHA-pinned callers adopt updates only when their pinned SHA changes. Nested workflow calls use the central workflow's revision; checkout always retrieves the consuming repository's code.

```mermaid
flowchart LR
    A[Validate configuration and discover projects] --> B[Unit tests and builds]
    A --> C[Browser and visual tests]
    A --> D[Dockerfile checks and image builds]
    A --> E[Docs, generation, integration and custom checks]
    A --> F[Workflow lint and security analysis]
    B --> G[Quality gate]
    C --> G
    D --> G
    E --> G
    F --> G
    G --> H[Explicitly enabled PyPI release]
```

## Commit message policy (automatic)

The aggregate workflow now enforces [semantic commit messages](https://gist.github.com/joshbuchea/6f47e86d2510bce28f8e7f42ae84c716) for **every PR commit**, not just the PR title. It also checks commits introduced by pushes to the default branch. The commit-message job is a mandatory dependency of the existing **Quality gate**, so aggregate callers only need to update their pinned shared-workflow SHA. No configuration file or secret is required; the documented aggregate caller already grants `pull-requests: read`.

Required first-line format: `<type>(<scope>): <subject>`. Scope is optional; the type must be lowercase and one of `chore`, `docs`, `feat`, `fix`, `refactor`, `style`, or `test`. The subject must be nonempty and start with a non-whitespace character. Commit body/footer content is unrestricted.

```text
feat: add user authentication
fix(api): handle timeout
docs: describe setup
test(parser): cover invalid input
```

`build: update dependencies`, `Feat: add login`, `feat add login`, `fix:`, and `feat!: break API` are **not accepted** by this seven-type policy. Use `chore:` for build/CI/dependency updates. Fix an existing commit with `git commit --amend -m "fix: describe change"`, or an earlier commit with interactive rebase; force-push only your own PR branch after coordinating with collaborators. For squash merges, use a compliant squash commit subject (typically the PR title).

GitHub-generated merge commits (multiple parents) are excluded from message validation; their individual PR commits are checked before merge. Synthetic merge-queue commits and unrelated scheduled, release, or manual events do not revalidate commit subjects. PRs or pushes exceeding GitHub's 250-commit REST API comparison limit fail closed rather than silently passing unchecked commits.

**Enforcement requires a branch ruleset:** configure **quality / Quality gate** as a required status check with PR checks and, if used, merge queue checks enabled. CI reports violations on direct default-branch pushes after the push; branch protection or rulesets must separately prevent bypassing PR review. SHA-pinned consumers will only receive this policy after updating their pin.

### Call commit validation with individual shared jobs

If a consumer calls `python.yml`, `security.yml`, `project-check.yml`, or other individual workflows **instead of the aggregate `ci.yml`**, updating its SHA pins alone does not enable the commit-message gate. Add this job alongside the other jobs in the consumer's existing workflow:

```yaml
  commit-messages:
    name: Commit message format
    uses: eslam-aboutaleb/ci-workflows/.github/workflows/commit-messages.yml@COMMIT_SHA
    permissions:
      contents: read
      pull-requests: read
```

Use the exact reviewed full SHA used for other shared jobs. Then require the emitted `commit-messages / Commit message format` check (use the actual name displayed in the repository's checks). The calling workflow must run on `pull_request`; include `merge_group` if the repository uses a merge queue. This new reusable workflow does not execute application code or require checkout or additional secrets.

## Discovery and project configuration

The aggregate workflow accepts `config-file` (default `.github/ci.json`) and `changes-only` (default `false`). Configuration is optional. Invalid JSON or TOML, duplicate JSON keys, unknown options, conflicting lockfiles, and settings for nonexistent projects fail discovery before checks start. A job summary records the discovered and selected checks.

| Evidence in the consuming repository | Selected check |
| --- | --- |
| `pyproject.toml` | Python quality; uv when `uv.lock` exists, otherwise editable installation through uv |
| `package.json` with a local npm, pnpm, or Bun lockfile | Node quality |
| A workspace package with no local lockfile | Covered by its owning workspace root; the summary lists these members |
| A standalone `package.json` without a supported lockfile | Discovery fails instead of silently omitting tests |
| `test:e2e`, `test:visual`, or `test:a11y` package scripts | Playwright/browser checks for each matching script |
| `Dockerfile`, `Dockerfile.*`, or `*.Dockerfile` | One image validation and build per Dockerfile |
| `compose.yaml`, `compose.yml`, or configured compose files | Docker Compose config validation and optional platform manifest checks |
| `*.tf` or `*.tm.hcl` | Terraform format/validation and Terramate generation drift checks |
| `ci-docs` or `ci-docs-links` Makefile targets | Documentation build/tests or link verification |
| `ci-generated` Makefile target | Regenerate files and reject drift |
| `ci-quality` Makefile target | Project-owned quality checks for any other stack |
| `ci-install`, `ci-migrate`, and `ci-integration` Makefile targets | PostgreSQL and Redis integration checks |
| `ci-eval` Makefile target | Provider evaluations on trusted default-branch push/manual runs |
| `.github/workflows/*.yml` or `*.yaml` | Actionlint in the consuming repository |
| Supported source files and Actions definitions | CodeQL language matrix; dependency review also runs on pull requests |

Node workspaces use the root's locked install and required scripts. The root scripts must test, typecheck and build all workspace members. Both `package.json` workspaces and pnpm workspace inclusion/exclusion patterns are recognized. Lockless Node projects can instead be covered by an enclosing `ci-quality` contract for package managers such as Yarn. Go, Rust, Java/Gradle/Maven, .NET, and Ruby manifests require an enclosing `ci-quality` contract because CodeQL alone does not run their unit tests.

Dependency, environment, build output, and vendored directories are excluded from discovery. This is convention-based discovery, not proof that every source file is exercised. Use project-owned contracts for stacks or layouts outside these conventions.

Start with [the fullstack configuration](.github/examples/ci.json) and keep only entries that exist in your repository. The [JSON schema](.github/ci.schema.json) supports editor validation. Project keys are normalized repository-relative paths (`.` for the root); Docker keys identify the exact Dockerfile.

```json
{
  "version": 1,
  "python": {
    "backend": {
      "python-versions": ["3.12", "3.13"],
      "coverage-source": "app",
      "coverage-min": 85,
      "pip-extras": "test"
    }
  },
  "node": {
    "frontend": {
      "node-versions": ["22", "24"],
      "operating-systems": ["ubuntu-latest", "windows-latest"]
    }
  },
  "browser": {
    "frontend": {"shards": 2, "browsers": "chromium firefox"}
  },
  "docker": {
    "backend/Dockerfile": {"build-context": "backend"},
    "frontend/Dockerfile": {"build-context": "frontend"}
  }
}
```

Python configuration supports version/OS arrays, `test-path`, `pip-extras`, `pip-constraints`, `coverage-source`, `coverage-min`, `build-distribution`, and `publish-pypi`. Distribution checks default to enabled only when a `[build-system]` table exists. Publishing defaults to disabled. Set both coverage fields to enforce a threshold; unset coverage fields provide no coverage threshold.

Node configuration supports version/OS arrays, `test-shards`, `pnpm-version`, `bun-version`, and `audit-level`. Browser configuration supports `test-script`, `node-version`, package manager versions, `browsers`, and `shards`. Docker configuration supports `build-context`, `platforms`, `target`, and a `build-args` array of non-secret `KEY=value` strings. Contexts default to the repository root; configure backend/frontend contexts explicitly when their `COPY` paths require them. Each Dockerfile builds its final stage unless `target` selects another stage; unused stages are not independently built.

### Optional change filtering

To reduce monorepo CI cost, set `changes-only: true` on the aggregate call and define `paths` globs for selected Python, Node, browser, Docker, or custom-contract entries. Include every shared dependency path used by those projects. Entries without `paths` always run. Security and workflow lint always run when applicable. Configuration/workflow changes and changes to root files invalidate filtering for all projects. Failed diff detection falls back to a full run. Pushes, releases, schedules, merge queues, and manual runs always run the full selected suite.

Filtering is off by default because the pipeline cannot infer your dependency graph. The final gate still runs when no changed project needs a matrix job.

## Workflow contracts

Every shared workflow is callable individually through `workflow_call`. Callers must supply the required inputs and permissions; definitions under `on.workflow_call.inputs` are the authoritative reference.

| Workflow | Required inputs | Behavior |
| --- | --- | --- |
| [ci.yml](.github/workflows/ci.yml) | None | Discovery, selected checks, summaries, stable final gate, commit-message check |
| [commit-messages.yml](.github/workflows/commit-messages.yml) | None | Check semantic subjects on PR commits and direct pushes; skip unrelated event types |
| [python.yml](.github/workflows/python.yml) | `project-dir`, `manager` (`uv`/`pip`) | Ruff lint/format, pytest on each Python/OS combination, optional coverage threshold and distributions; JUnit/coverage artifacts |
| [node.yml](.github/workflows/node.yml) | `project-dir`, `manager` (`npm`/`pnpm`/`bun`) | Frozen install, dependency audit, mandatory lint/typecheck/test/build; optional OS and shard matrices; available test/coverage artifacts |
| [browser.yml](.github/workflows/browser.yml) | `project-dir`, `manager` | Locked Playwright install, browser or visual script, shards, available reports/traces/screenshots |
| [docker.yml](.github/workflows/docker.yml) | None | Dockerfile build checks and image builds, optional stages/platforms/arguments, scoped BuildKit cache; no registry credentials |
| [compose.yml](.github/workflows/compose.yml) | None | Docker Compose configuration validation and optional multi-arch image manifest verification |
| [terraform.yml](.github/workflows/terraform.yml) | `project-dir` | Terraform format/validate across modules and stacks, plus Terramate format and generation drift checks |
| [image-verification.yml](.github/workflows/image-verification.yml) | Explicit images or Compose files | Opt-in immutable image/platform checks, image SBOMs, vulnerability scans and optional Cosign verification |
| [terraform-plan.yml](.github/workflows/terraform-plan.yml) | `project-dir`, `stack-id`; named encryption secret | Opt-in remote-state plan with encrypted, short-lived artifacts |
| [terraform-apply.yml](.github/workflows/terraform-apply.yml) | Stack, environment and approved plan identity; named encryption secret | Verify run/artifact/commit provenance and apply the exact reviewed binary; never replan |
| [terraform-drift.yml](.github/workflows/terraform-drift.yml) | `project-dir`, `stack-id`; named encryption secret | Opt-in drift output, encrypted diagnostics and configurable failure on drift |
| [project-check.yml](.github/workflows/project-check.yml) | `project-dir`, `target` | Run `ci-install`, then the chosen Make target; optional generated-file drift check |
| [integration.yml](.github/workflows/integration.yml) | `project-dir` | Healthy PostgreSQL/Redis services, install, migrate, integration tests |
| [ai-eval.yml](.github/workflows/ai-eval.yml) | `project-dir` | Install Python/Node/uv tooling, run `ci-eval`; optional named provider key |
| [security.yml](.github/workflows/security.yml) | None | CodeQL, compiled-language builds, Gitleaks secret scan, optional caller-owned configuration, dependency review on PRs |
| [workflow-lint.yml](.github/workflows/workflow-lint.yml) | None | Actionlint against the consuming repository's workflows |
| [publish-docker.yml](.github/workflows/publish-docker.yml) | `image` | Event validation, image build validation, approved GHCR publication, provenance, SBOM and digest output |
| [publish-pypi.yml](.github/workflows/publish-pypi.yml) | `project-dir`, `distribution-name` | Match release tag to package metadata, build/check distributions, approved publication using `PYPI_API_TOKEN` |
| [validate.yml](.github/workflows/validate.yml) | Repository maintenance only | Actionlint, shared contract checks, discovery and gate regression tests |
| [propagate.yml](.github/workflows/propagate.yml) | Repository maintenance only | Automatically open PRs updating pinned `ci-workflows` SHAs across downstream repositories registered in the `CI_DOWNSTREAM_REPOSITORIES` Actions secret |
| [operations-smoke.yml](.github/workflows/operations-smoke.yml) | Repository maintenance only | Real local-state Terraform plan/apply and public multi-platform image verification |

The new image and remote-state workflows are separate explicit calls. They do not
change discovery, `.github/ci.json`, existing workflow defaults, the quality gate,
or existing callers' required permissions. Infrastructure credentials, schedules,
environment protection and notification policy belong to the consuming repository.
See the [operations contracts](docs/shared-operations.md) before enabling them.

Python uv projects must lock Ruff, pytest and pytest-cov when coverage is enabled. pip projects use an editable install; install test extras or provide a constraints file as appropriate. Node projects must define noninteractive `lint`, `typecheck`, `test`, and `build` scripts. Audits fail at `high` by default; use `moderate` for a stricter threshold. Bun must have an exact runtime version in `packageManager: bun@<version>` or `bun-version`. pnpm uses `packageManager` or an explicit `pnpm-version`.

`operating-systems` accepts `ubuntu-latest`, `windows-latest`, and `macos-latest`; defaults remain Linux. Node `test-shards` defaults to one. When greater than one, the project's `test` script must actually partition tests using `CI_SHARD_INDEX` and `CI_SHARD_TOTAL`. Lint, typecheck, audit, and build run once per Node/OS combination; all test shards must pass.

Browser scripts must use a declared local Playwright dependency. Their configuration must start the application and services and select the browser projects to test. `browsers` controls installed binaries; the Playwright configuration controls which projects execute. `shards > 1` forwards `--shard=index/total` to the script. Visual checks compare reviewed baselines; CI does not update or commit snapshots. Available `playwright-report`, `test-results`, and `blob-report` directories are retained for seven days, including on failure. Python/Node reports are retained for seven days when produced.

### Documentation, generation, and other stacks

Provide a Makefile with explicit targets. `ci-install` installs project tooling and dependencies with your lockfile. Each check gets a clean checkout and an independent installation.

```makefile
.PHONY: ci-install ci-docs ci-docs-links ci-generated ci-quality

ci-install:
	npm ci

ci-docs:
	npm run docs:build
	npm run docs:test

ci-docs-links:
	npm run docs:links

ci-generated:
	npm run generate

ci-quality:
	npm run lint
	npm run typecheck
	npm test
	npm run build
```

For another stack, make `ci-install` install its pinned toolchain/dependencies and make `ci-quality` run its formatter, linter, type checks, unit tests, audit, and build as appropriate. `project-check.yml` provides Python, Node and uv. Optional `pnpm-version` and `bun-version` inputs install those tools. Project-specific Java, Rust, Go, .NET or other toolchains belong in `ci-install`.

Generation fails on modified/deleted tracked files, staged changes, or newly created unignored files anywhere in the checkout. Commit generated sources and ignore transient build outputs. `contracts` configuration can add custom `ci-*` targets, set runtime versions or paths, and label them `quality`, `docs`, or `generated`. Generated contracts always enforce drift checking.

```json
{
  "version": 1,
  "contracts": [
    {"kind": "docs", "project-dir": "docs", "target": "ci-docs", "pnpm-version": "10.10.0"},
    {"kind": "generated", "project-dir": ".", "target": "ci-generated"},
    {"kind": "quality", "project-dir": "agent", "target": "ci-eval-offline"}
  ],
  "security": {
    "config-file": ".github/codeql-config.yml",
    "build-command": "make ci-codeql"
  }
}
```

Provider AI evaluations run on trusted default-branch pushes/manual runs and receive only `AI_EVAL_API_KEY`, mapped to the evaluation step. `ci-eval` installs and runs the suite and must return nonzero when scores violate project thresholds. Use an additional secret-free quality contract, such as `ci-eval-offline`, for PR evaluations. The central workflow cannot choose datasets or meaningful evaluation thresholds for each application.

Integration targets receive `CI_POSTGRES_URL` and `CI_REDIS_URL` for disposable services. Individually called integration workflows accept `postgres-image` and `redis-image`; defaults are `postgres:16` and `redis:7`. PostgreSQL extensions may require a compatible image, for example pgvector.

CodeQL keeps the `security-extended` suite. Its optional `config-file` is resolved inside the consuming repository. Compiled C/C++, C#, Go, Java/Kotlin and Swift use autobuild or the configured `build-command`; Swift runs on macOS. Rust uses analysis without a build. Multi-language custom build commands must prepare every compiled language they cover. Source analysis complements the project's unit-test/build contract. Dependency review and CodeQL require the corresponding GitHub security features and licensing; unavailable features fail their selected jobs.

## Publishing

Verification and publishing have separate permissions and environments. Configure required reviewers and deployment branch/tag restrictions before enabling a `pypi` or `containers` environment. Creating an environment in YAML does not configure its protection rules.

To enable aggregate PyPI publication, set `"publish-pypi": true` for the intended Python project and include `release: types: [published]` in the caller. Final releases publish only after Quality gate passes, and only if the tag is exactly `v<project.version>`. Store a PyPI API token authorized for the target distribution as the consuming repository's `PYPI_API_TOKEN` Actions secret, pass it explicitly to the workflow, and configure required reviewers on the `pypi` environment. The token reaches only the approved upload job. PyPI OIDC publication requires an upload job in the consuming repository's own workflow; it is not supported from a called workflow. Token publication does not generate PyPI attestations. In monorepos, enable only packages intended to share that release tag, or use separate direct publication jobs.

Container publication is an explicit separate job after the aggregate caller completes successfully:

```yaml
  publish-container:
    needs: quality
    if: github.event_name == 'release' && github.event.action == 'published' && !github.event.release.prerelease
    uses: eslam-aboutaleb/ci-workflows/.github/workflows/publish-docker.yml@COMMIT_SHA
    permissions:
      contents: read
      packages: write
    with:
      image: ghcr.io/your-owner/your-image
      dockerfile: backend/Dockerfile
      build-context: backend
      platforms: linux/amd64,linux/arm64
```

`image` must be lowercase and owned by the consuming repository's owner. The workflow accepts final published releases or pushes/manual runs on a protected default branch. It uses the `containers` environment, pushes a full commit SHA tag plus the release tag when applicable, generates provenance and an SBOM, and returns `digest` for deployment by immutable digest. Grant the repository write access to an existing GHCR package when needed. No `latest` tag is created. Publication rebuilds the validated source; pin base images by digest when reproducibility is required. Neither Docker builds nor SBOM generation provide a container vulnerability scan or runtime smoke test; add those to your project contract where required.

## Maintaining the central repository

External actions use full commit SHAs. Dependabot proposes action updates weekly; review their runtime, input and permission changes before merging. The workflows use Node 24 compatible actions and require a compatible GitHub hosted runner; the actions' baseline is runner v2.327.1 or later. Artifact actions v7/v8 are not supported on GitHub Enterprise Server, so this suite targets GitHub.com.

`CODEOWNERS` assigns this repository's workflow/configuration reviews to its maintainer. Consuming repositories need their own ownership rules. The PR template records caller compatibility and validation evidence.

Run the repository's maintenance checks locally with Python, PyYAML, Go and actionlint available:

```sh
make ci-install
make ci-quality lint
```

The regression suite exercises discovery on temporary fullstack/workspace repositories, invalid configurations, change filtering, and required-check failures. Workflow syntax and fixture tests do not replace a real GitHub Actions run against a representative consuming repository. Review the selected-check summary, enable the ruleset, and update consuming SHA pins deliberately.

Use Python 3.11 or later. Maintenance implementation lives in `scripts/maintenance/`
and regression tests in `tests/`; the former `.github/scripts/` commands and
`.github/requirements-ci.txt` remain compatible entry points. Existing reusable
workflow paths and the public schema URL remain stable. The
[architecture guide](docs/architecture.md) describes dependency pinning and the
folder boundaries.

Repository tooling uses uv and a local `.venv`. Install uv, then run `make ci-install` and `make ci-quality` (Python 3.11 or newer). Refresh the universal, hash-locked dependencies with `make lock` after editing `requirements/ci.in`. Existing `manager: pip`, `pip-extras`, and `pip-constraints` inputs remain supported; their installations now use uv’s pip-compatible interface. Distribution builds also use uv for isolated build dependencies.

### Database credentials and token scanning

`security.yml` and aggregate `ci.yml` enable Gitleaks by default. The standalone
`secret-scanning.yml` requires only `contents: read` and no application secrets.
The caller chooses triggers; scheduled and manual full audits run only when the
caller defines those events.

PR, push, and merge-queue checks scan every introduced commit using GitHub's
base/head SHAs. A credential added and then deleted within that range still
fails. Scheduled/manual audits and events without an available base scan the
complete history reachable from the checked-out commit. Existing historical
findings therefore do not block an unrelated PR, while full audits still expose
them. New branches and unavailable force-push bases fall back to a full audit.

The shared policy extends Gitleaks' built-in token rules and detects passwords in
database URLs. Its exceptions are independent of any application: environment
references, conventional disposable values on loopback/local service hosts, and
conventional example values on reserved documentation domains. A real-looking
credential still fails on localhost or an example domain; conventional test
values still fail on real remote hosts. Explicit synthetic markers and public
40-hex blockchain addresses are excluded only from the generic API-key heuristic;
provider-specific token rules remain active, including in tests and examples.
No directory is excluded just because it contains tests or documentation.

Failures print the rule, path, line, and fingerprint with credentials redacted.
The downloaded scanner checksum is verified. A caller's `.gitleaks.toml` cannot
replace the central policy. For remaining verified false positives, use a narrow
inline `gitleaks:allow` comment or an exact `.gitleaksignore` fingerprint. Prefer
inline annotations for fixtures that must survive squash merges, since commit
fingerprints change after squashing. Never exempt whole directories or genuine
credentials. Require the scanner check in the consuming repository's ruleset;
rotate any actually exposed credential at its provider.


### Downstream publishing bot

Configure repository Actions secrets in `eslam-aboutaleb/ci-workflows`:

- `CI_PROPAGATION_TOKEN`: a token allowed to read downstream repositories, push
  workflow changes and open/update pull requests (including workflow write permission).
- `CI_DOWNSTREAM_REPOSITORIES`: the authoritative JSON registry, with format
  `{"version":1,"repositories":[{"repo":"owner/project"}]}`. String entries
  (`"owner/project"`) are also accepted. Preserve existing entries when adding callers.

Set the registry using `gh secret set CI_DOWNSTREAM_REPOSITORIES --repo
eslam-aboutaleb/ci-workflows < registry.json` from a private local file. The
registry is no longer committed to this repository; do not commit that local file.
Automatic propagation and all-repository dry runs fail if the registry is missing,
empty or invalid. An explicit dispatch `repository` override targets just that
repository without loading the registry. Dry runs still need read access for
private repositories. Adding a registry entry does not install workflows: each
consumer must first call a reusable workflow using a full commit SHA.

Generated PRs include description, change type, compatibility, verification,
independent review and checklist sections. Verification is explicitly pending
until downstream checks complete; the bot never claims application tests or
independent review passed. Each PR includes the changed pins and upstream compare
links for maintainer review.


### Pull request description schema

`pr-description.yml` enforces the same six-section description contract as ragnor
(ragit): Description, Type of Change, Behavior and Compatibility, Verification
Evidence, Independent Review and Checklist. It requires exactly one official
change type, all checklist items, and concrete test evidence with a result or an
explicit pending/unverified status. Copy `.github/pull_request_template.md` into
consumers and call the reusable workflow on pull request `opened`, `edited`,
`reopened`, `synchronize` and `ready_for_review` events. Description edits rerun
validation without a new commit. As in ragit, automation accounts ending in
`[bot]` are exempt; PAT-authored publishing PRs still validate.

The validator reads the event JSON as data and executes no consumer checkout or
PR-provided code. The inline workflow implementation is checked against
`scripts/check_pr_description.py` in the regression suite. Configure its status
as required on `main` to prevent merging descriptions that fail validation.


### Pull requests with merge conflicts

Use `pull_request` for PR checks and restrict `push.branches` to `[main]` in
consuming callers, as shown in `.github/examples/caller.yml`. GitHub does not
start `pull_request` runs while a PR has merge conflicts. Unrestricted branch
`push` triggers bypass that suppression, including when they call a reusable
workflow. Trigger filters belong in the caller; the callee cannot prevent a
caller workflow from starting. The contract checker enforces this policy for
this repository's workflows and caller examples.

Do not use `pull_request_target` for these checks. Existing runs are not
retroactively canceled when a base update introduces conflicts. Manual,
scheduled, release and main-branch runs remain available independently of PR
mergeability. Resolve conflicts and push the resolution to resume PR checks.
