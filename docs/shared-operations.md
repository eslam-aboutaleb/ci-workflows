# Shared operations contracts

These workflows are independent, opt-in calls. Existing quality workflows and
`.github/ci.json` behavior are unchanged. Copy the [consumer examples](../examples/)
and replace `COMMIT_SHA` with one reviewed full workflow commit SHA.

## Image verification

[`image-verification.yml`](../.github/workflows/image-verification.yml) accepts
either `images`, a JSON array of registry image references, or `project-dir` and
`files` for rendered Docker Compose configuration. `files` is a JSON array relative
to `project-dir`. With an empty `images` array, every service image in the rendered
Compose model is selected. Supply any required interpolation values using the
consumer's checked-in, nonsecret Compose defaults; this workflow accepts no
production secrets or private registry credentials.

`platforms` defaults to `linux/arm64`; supported values are `linux/amd64`,
`linux/arm64`, and `linux/arm/v7`, separated by commas. Every selected platform
must exist in the exact registry manifest. Existing digest pins are preserved;
tag references are resolved to immutable digests before scanning. At most 64
image/platform combinations are allowed. A native runtime smoke test is separate
from manifest compatibility and vulnerability verification.

Each image/platform gets an image SPDX JSON SBOM and a vulnerability SARIF report.
Trivy fails on `HIGH,CRITICAL` by default, including unfixed findings; `severity`
can select other severities. Reports remain available as seven-day artifacts even
when the vulnerability scan fails. SBOM JSON is never sent to a SARIF endpoint.
The workflow scans public registry content; it does not build or publish images.

Set `verify-signatures: true` and provide either a consumer-owned `cosign-key`
public key file, or both an exact `certificate-identity` and
`certificate-oidc-issuer`. A missing or ambiguous trust policy fails preflight.
For images from different signing authorities, use separate calls with their own
explicit image lists. The default performs no signature verification.

Set `upload-sarif: true` to also send vulnerability reports to GitHub code scanning.
That feature must be available for the consumer repository. Fork PRs retain
artifacts but do not upload SARIF. The caller must grant `contents: read` and
`security-events: write` because the verification job declares both scopes.

## Terraform plan, apply and drift

The three workflows share a single versioned execution action. All accept:

| Input | Contract |
| --- | --- |
| `project-dir` | Required repository-relative Terraform stack directory |
| `stack-id` | Required stable identifier; use the same value for every operation on one state |
| `terraform-version` | Exact version, default `1.12.2` |
| `terramate-version` | Exact version; empty disables root-level `terramate generate` |
| `backend-config` | JSON object of nonsecret backend settings; no bucket creation or bootstrap |
| `oci-config` | Optional JSON object with exactly `user`, `tenancy`, `fingerprint`, `region` |
| `environment` | Consumer environment name; apply requires an explicit value |

Pass named secrets explicitly:

| Secret | Contract |
| --- | --- |
| `PLAN_ENCRYPTION_KEY` | Required random 64-character hexadecimal AES-256 key; same value for plan/apply |
| `TF_VARS_JSON` | Optional JSON object of Terraform variables; pass the same values for plan/apply |
| `OCI_API_KEY` | Required when `oci-config` enables OCI API-key authentication |

Generate the encryption key with `openssl rand -hex 32` and store it as an Actions
secret. Key rotation invalidates previously encrypted plans; generate a new plan
after rotation. Each plan and diagnostic bundle uses authenticated AES-GCM
encryption with a fresh nonce. Plaintext plans are removed before artifact upload.
Artifacts contain only `plan.enc` and expire after one day. A job summary identifies
the stack, commit and whether changes exist, without displaying raw plan contents.
To inspect a downloaded artifact locally, use the pinned action's `protect.py`
with the key supplied through an environment variable:

```sh
PLAN_DIR=/absolute/path/to/downloaded-artifact \
PLAN_ENCRYPTION_KEY="$LOCAL_PLAN_KEY" \
python3 .github/actions/terraform-operation/protect.py decrypt
```

Review `plan.txt` locally and handle the decrypted binary and text as secrets.
The script requires the pinned `cryptography` dependency from `requirements/ci.txt`.

The plan workflow supports same-repository PRs, pushes and manual callers. Fork PRs
are skipped before credentials are used. PR plans are speculative and cannot be
applied through the shared apply workflow. For an applicable plan, run the plan
caller on the default branch and allow the entire source run to complete
successfully. Returned outputs are `has-changes`, `plan-sha`, `plan-run-id` and
`plan-artifact-id`. Keep plan and apply as separate caller runs.

The apply caller must be manually dispatched on the protected default branch and select a consumer
environment with required reviewers and branch restrictions. It needs
`contents: read` and `actions: read`. Supply `plan-sha`, `plan-run-id`, the exact
`plan-artifact-id`, and `plan-workflow`, the expected consumer caller path such as
`.github/workflows/infra-plan.yml`. Source validation requires a successful
push/manual plan from this repository's default branch and that exact caller
workflow. Apply checks out the approved SHA, downloads the immutable artifact ID,
decrypts it, checks the checksum and configuration manifest, initializes the same
backend, then applies the saved binary. Missing, expired, altered or mismatched
plans fail; there is no fallback to a fresh plan. Decrypted files and temporary OCI
credential files are removed after use. Environment protection and least-privilege
cloud IAM remain the consumer's responsibility.

Plan and drift require only `contents: read`. The drift workflow returns
`has-changes` and an encrypted artifact; by default it fails on drift. Set
`fail-on-drift: false` to handle that output in a separate consumer notification
job. Terraform exit code 1 always fails; it is never treated as no drift. These
workflows do not create issues or send notifications themselves.

All three serialize operations with a job concurrency group built from repository
and `stack-id`, without cancelling state operations. Terraform backend locking is
still required: GitHub concurrency does not coordinate jobs outside these calls.

## OCI credential action

[`setup-oci`](../.github/actions/setup-oci/action.yml) can also be used directly by
consumer-owned release or backup workflows. It encrypts the signing key with a
fresh masked passphrase, writes mode-0600 files into an isolated runner temporary
directory, and exports CLI and Terraform authentication
variables. It installs no OCI CLI and creates no cloud resources. Use a separately
pinned CLI installation if your operation needs it. Follow it with an `always()`
call to the same action with `cleanup: 'true'`. The Terraform operation action
already performs that cleanup internally.
