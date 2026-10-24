# Gateway adoption plan

The shared implementation is additive. Existing consumers keep their current
workflow pins, settings and required-check names. No gateway runtime operation is
enabled by merging these shared workflow definitions.

| Gateway workflow | Adoption |
| --- | --- |
| `ci.yml` | Keep the current thin caller; update its SHA only after shared checks pass |
| `arm64-compatibility.yml` | Replace its implementation with `image-verification.yml`; keep local triggers and explicit signing policy |
| `infra-plan.yml` | Call `terraform-plan.yml` with OCI backend settings and stack variables |
| `infra-apply.yml` | Call `terraform-apply.yml` with explicit source run, artifact ID, SHA and production environment |
| `drift.yml` | Call `terraform-drift.yml`; keep scheduling and issue/notification handling local |
| `release.yml` | Keep the LiteLLM release manifest and packaging local; optionally reuse OCI credential setup |
| `deploy.yml` | Keep gateway deployment and configuration local |
| `smoke-test.yml` | Keep application, model, route and network assertions local |
| `admin-operations.yml` | Keep LiteLLM administration local |
| `backup-restore.yml` | Keep database backup/restore orchestration local |
| `disaster-recovery-test.yml` | Keep recovery policy and operator drill local |

Adopt one capability at a time. Start with a separately named image verification
caller so its reports and trust rules can be reviewed before removing duplicated
checks. Next configure the read-only infrastructure environment, OCI identity,
Terraform variables and plan encryption key. Confirm planning and drift against
the existing backend. Bootstrap state buckets separately; the shared plan does not
create them.

Finally configure production environment reviewers and branch restrictions, then
exercise saved-plan application in a disposable stack before production adoption.
Preserve exact Terraform/Terramate versions between planning and application.
The existing gateway apply workflow's fresh-plan fallback is intentionally not
part of the shared contract.

Once equivalent coverage is demonstrated, remove duplicate Terraform format and
validation work from the gateway's Makefile, relying on existing shared
`terraform.yml` validation. Keep model-catalog, Caddy routing, resource-hardening,
documentation and other application-specific assertions in local Make targets.
