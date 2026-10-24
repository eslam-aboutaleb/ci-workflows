"""Terraform operations with immutable plan provenance and no implicit replanning."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def output(name, value):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write(f'{name}={value}\n')


def object_json(value, label):
    result = json.loads(value)
    require(isinstance(result, dict), f'{label} must be a JSON object')
    require(all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', key) for key in result), f'Invalid {label} key')
    return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def verify_source(repository, run_id, artifact_id, sha, workflow, default_branch, fetch=api):
    require(re.fullmatch(r'[0-9a-f]{40}', sha), 'plan-sha must be a full commit SHA')
    require(re.fullmatch(r'[1-9][0-9]*', run_id) and re.fullmatch(r'[1-9][0-9]*', artifact_id), 'Run and artifact IDs must be positive integers')
    require(re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', workflow), 'plan-workflow must be a caller workflow path')
    run = fetch(f'repos/{repository}/actions/runs/{run_id}')
    require(run['conclusion'] == 'success' and run['status'] == 'completed', 'Plan run must have completed successfully')
    require(run['head_sha'] == sha and run['head_branch'] == default_branch, 'Plan must be for the requested default-branch commit')
    require(run['event'] in {'push', 'workflow_dispatch'}, 'Apply requires a trusted push/manual plan, never a PR plan')
    require(run['path'] == workflow, 'Plan run came from a different workflow')
    require(run['repository']['full_name'] == repository and run['head_repository']['full_name'] == repository, 'Plan must originate in this repository')
    artifact = fetch(f'repos/{repository}/actions/artifacts/{artifact_id}')
    require(not artifact['expired'], 'Reviewed plan artifact has expired')
    require(artifact['workflow_run']['id'] == int(run_id) and artifact['workflow_run']['head_sha'] == sha, 'Artifact does not belong to the approved plan run/commit')
    require(artifact['name'].startswith('terraform-plan-'), 'Unexpected plan artifact name')
    return sha


def checked_project(value, root=None):
    root = (root or Path.cwd()).resolve()
    path = Path(value)
    require(value and '\n' not in value and '\r' not in value, 'Invalid project-dir')
    require(not path.is_absolute() and '..' not in path.parts, 'project-dir must be repository-relative')
    result = (root / path).resolve()
    require(result.is_relative_to(root) and result.is_dir(), 'project-dir must exist inside the checkout')
    return result


def run(arguments, cwd, env, **kwargs):
    return subprocess.run(arguments, cwd=cwd, env=env, check=True, **kwargs)


def verify_manifest(manifest, plan, expected):
    require(plan.is_file() and not plan.is_symlink(), 'Reviewed binary plan is missing')
    require(manifest.get('version') == 1, 'Unsupported plan manifest')
    for key, value in expected.items():
        require(manifest.get(key) == value, f'Reviewed plan {key} does not match this apply')
    require(manifest.get('plan_sha256') == hashlib.sha256(plan.read_bytes()).hexdigest(), 'Binary plan checksum mismatch')


def execute(mode):
    require(mode in {'plan', 'apply', 'drift'}, 'Unsupported Terraform operation')
    root = Path.cwd().resolve()
    project = checked_project(os.environ['PROJECT_DIR'], root)
    backend = object_json(os.environ.get('BACKEND_CONFIG', '{}'), 'backend-config')
    require(all(isinstance(value, (str, int, bool)) for value in backend.values()), 'Backend settings must be scalar values')
    variables = object_json(os.environ.get('TF_VARS_JSON', '{}'), 'tf-vars')
    env = {**os.environ, 'TF_IN_AUTOMATION': 'true', 'TF_INPUT': 'false'}
    # GitHub env maps reject names differing only by case. Terraform 1.12's
    # backend needs lowercase OCI attributes, while the provider needs uppercase.
    # Add backend aliases only inside the Linux Terraform subprocess environment.
    for name in ('USER_OCID', 'TENANCY_OCID', 'FINGERPRINT', 'REGION', 'PRIVATE_KEY_PATH', 'PRIVATE_KEY_PASSWORD'):
        if env.get('OCI_' + name):
            env['OCI_' + name.lower()] = env['OCI_' + name]
    env.update({f'TF_VAR_{key}': value if isinstance(value, str) else json.dumps(value) for key, value in variables.items()})
    version = json.loads(subprocess.check_output(['terraform', 'version', '-json'], env=env, text=True))['terraform_version']
    require(version == os.environ['TERRAFORM_VERSION'], 'Installed Terraform version differs from the pinned version')
    expected = {
        'repository': os.environ['GITHUB_REPOSITORY'],
        'source_sha': os.environ.get('PLAN_SHA') if mode == 'apply' else os.environ['GITHUB_SHA'],
        'project_dir': project.relative_to(root).as_posix(),
        'stack_id': os.environ['STACK_ID'],
        'terraform_version': version,
        'backend_sha256': fingerprint(backend),
        'variables_sha256': fingerprint(variables),
    }
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    require(head == expected['source_sha'], 'Checkout does not match the plan commit')
    plan_dir = Path(os.environ.get('PLAN_DIR') or tempfile.mkdtemp(prefix='tf-plan-', dir=env['RUNNER_TEMP'])).resolve()
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_dir / 'tfplan'
    if mode == 'apply':
        manifest_path = plan_dir / 'manifest.json'
        require(manifest_path.is_file() and not manifest_path.is_symlink(), 'Reviewed plan manifest is missing')
        manifest = json.loads(manifest_path.read_text())
        verify_manifest(manifest, plan, {**expected, 'source_run_id': os.environ['PLAN_RUN_ID']})
    if os.environ.get('TERRAMATE_VERSION'):
        run(['terramate', 'generate'], root, env)
    with tempfile.TemporaryDirectory(prefix='tf-config-', dir=env['RUNNER_TEMP']) as directory:
        cfg = Path(directory) / 'backend.tfbackend'
        cfg.write_text('\n'.join(f'{key} = {json.dumps(value)}' for key, value in backend.items()) + '\n')
        cfg.chmod(0o600)
        run(['terraform', 'init', '-input=false', f'-backend-config={cfg}'], project, env)
        if mode == 'apply':
            run(['terraform', 'apply', '-input=false', str(plan)], project, env)
            return
        # Capture plan output rather than leaking configuration values into public logs.
        with open(plan_dir / 'plan.txt', 'w') as stream:
            result = subprocess.run(['terraform', 'plan', '-input=false', '-detailed-exitcode', f'-out={plan}'], cwd=project, env=env, stdout=stream, stderr=subprocess.STDOUT)
        require(result.returncode in {0, 2}, 'Terraform plan failed; inspect the restricted plan artifact')
        changed = result.returncode == 2
        require(plan.is_file(), 'Terraform did not produce a binary plan')
        manifest = {
            'version': 1, **expected,
            'source_run_id': os.environ['GITHUB_RUN_ID'],
            'plan_sha256': hashlib.sha256(plan.read_bytes()).hexdigest(),
            'has_changes': changed,
        }
        (plan_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        output('plan-dir', plan_dir)
        output('has-changes', str(changed).lower())
        with open(env['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(f'### Terraform {mode}\n\nStack: `{expected["stack_id"]}`\n\nCommit: `{head}`\n\nChanges detected: **{str(changed).lower()}**. Review the restricted plan artifact.\n')


if __name__ == '__main__':
    mode = os.environ['OPERATION']
    if mode == 'verify':
        require(os.environ['CALLER_REF'] == f'refs/heads/{os.environ["DEFAULT_BRANCH"]}' and os.environ['CALLER_REF_PROTECTED'] == 'true', 'Apply must run on the protected default branch')
        sha = verify_source(os.environ['GITHUB_REPOSITORY'], os.environ['PLAN_RUN_ID'], os.environ['PLAN_ARTIFACT_ID'], os.environ['PLAN_SHA'], os.environ['PLAN_WORKFLOW'], os.environ['DEFAULT_BRANCH'])
        output('plan-sha', sha)
    else:
        execute(mode)
