## Change

Describe the CI behavior being added or fixed and which consuming repositories it affects.

## Compatibility

Describe input, permission, runner, secret, or required-check changes. Include caller migration steps when needed.

## Validation

Record workflow lint and contract test results. For runtime changes, link a representative consuming repository run.

## Review

- [ ] External actions use reviewed full commit SHAs.
- [ ] Untrusted pull requests receive no publishing credentials or provider keys.
- [ ] New checks participate in the final quality gate.
- [ ] README and caller examples match the workflow contracts.
