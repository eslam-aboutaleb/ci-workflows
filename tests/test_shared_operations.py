"""Behavioral tests for shared operations without cloud credentials or registries."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.maintenance.check_workflows import ROOT, load


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


tf = module('terraform_operation', '.github/actions/terraform-operation/operation.py')
images = module('image_inventory', '.github/actions/image-inventory/inventory.py')
oci = module('oci_configuration', '.github/actions/setup-oci/configure.py')
protection = module('plan_protection', '.github/actions/terraform-operation/protect.py')


class CompatibilityTests(unittest.TestCase):
    def test_runtime_encryption_dependency_matches_maintained_requirement(self):
        requirement = next(line for line in (ROOT / 'requirements/ci.txt').read_text().splitlines() if line.startswith('cryptography=='))
        for action in ['terraform-operation', 'setup-oci']:
            self.assertIn(requirement, (ROOT / f'.github/actions/{action}/action.yml').read_text())

    def test_current_downstream_callers_still_match_shared_interfaces(self):
        callers = json.loads((ROOT / 'tests/fixtures/downstream-callers.json').read_text())
        for repo, jobs in callers.items():
            for job in jobs:
                with self.subTest(repository=repo, workflow=job['workflow']):
                    call = load(ROOT / '.github/workflows' / job['workflow'])['on']['workflow_call'] or {}
                    declared = call.get('inputs', {})
                    self.assertLessEqual(set(job['with']), set(declared))
                    required = {key for key, value in declared.items() if value.get('required') in {True, 'true'}}
                    self.assertLessEqual(required, set(job['with']))
                    self.assertLessEqual(set(job['secrets']), set(call.get('secrets', {})))

    def test_new_consumer_examples_match_required_inputs_and_secrets(self):
        for path in (ROOT / 'examples').rglob('*.yml'):
            workflow = load(path)
            for job in workflow['jobs'].values():
                if not job.get('uses', '').startswith('eslam-aboutaleb/ci-workflows/'):
                    continue
                with self.subTest(example=path.name):
                    name = job['uses'].split('/')[-1].split('@')[0]
                    call = load(ROOT / '.github/workflows' / name)['on']['workflow_call']
                    for section, supplied in [('inputs', job.get('with', {})), ('secrets', job.get('secrets', {}))]:
                        declared = call.get(section, {})
                        self.assertLessEqual(set(supplied), set(declared))
                        required = {key for key, value in declared.items() if value.get('required') in {True, 'true'}}
                        self.assertLessEqual(required, set(supplied))

    def test_existing_public_contracts_and_check_names_are_unchanged(self):
        baseline = json.loads((ROOT / 'tests/fixtures/public-contracts.json').read_text())
        for name, expected in baseline.items():
            with self.subTest(workflow=name):
                workflow = load(ROOT / '.github/workflows' / name)
                actual = {'workflow_call': workflow['on']['workflow_call'], 'permissions': workflow['permissions'], 'jobs': {key: job.get('name') for key, job in workflow['jobs'].items()}}
                self.assertEqual(actual, expected)

    def test_legacy_maintenance_imports_still_work(self):
        old = module('legacy_check', '.github/scripts/check_workflows.py')
        self.assertEqual(old.ROOT, ROOT)
        old = module('legacy_propagation', '.github/scripts/propagate_updates.py')
        self.assertEqual(old.DOWNSTREAM_CONFIG, ROOT / '.github/downstream.json')


class ProvenanceTests(unittest.TestCase):
    sha = 'a' * 40

    def data(self):
        return [
            {'conclusion': 'success', 'status': 'completed', 'head_sha': self.sha, 'head_branch': 'main', 'event': 'workflow_dispatch', 'path': '.github/workflows/infra-plan.yml', 'repository': {'full_name': 'owner/repo'}, 'head_repository': {'full_name': 'owner/repo'}},
            {'expired': False, 'name': 'terraform-plan-production-11-1', 'workflow_run': {'id': 11, 'head_sha': self.sha}},
        ]

    def verify(self, data):
        responses = iter(data)
        return tf.verify_source('owner/repo', '11', '22', self.sha, '.github/workflows/infra-plan.yml', 'main', fetch=lambda _: next(responses))

    def test_trusted_run_and_matching_artifact_are_accepted(self):
        self.assertEqual(self.verify(self.data()), self.sha)

    def test_untrusted_or_mismatched_runs_are_rejected(self):
        changes = [('conclusion', 'failure'), ('status', 'in_progress'), ('head_sha', 'b' * 40), ('head_branch', 'feature'), ('event', 'pull_request'), ('path', '.github/workflows/other.yml'), ('head_repository', {'full_name': 'fork/repo'})]
        for key, value in changes:
            with self.subTest(field=key):
                data = self.data()
                data[0][key] = value
                with self.assertRaises(ValueError):
                    self.verify(data)

    def test_expired_or_wrong_artifacts_are_rejected(self):
        for key, value in [('expired', True), ('workflow_run', {'id': 12, 'head_sha': self.sha}), ('workflow_run', {'id': 11, 'head_sha': 'b' * 40}), ('name', 'unrelated')]:
            with self.subTest(field=key):
                data = self.data()
                data[1][key] = value
                with self.assertRaises(ValueError):
                    self.verify(data)

    def test_manifest_and_binary_must_match_apply_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / 'tfplan'
            plan.write_bytes(b'reviewed-plan')
            expected = {'source_sha': self.sha, 'project_dir': 'infra', 'terraform_version': '1.12.2', 'backend_sha256': 'backend', 'variables_sha256': 'vars', 'source_run_id': '11', 'stack_id': 'production'}
            manifest = {'version': 1, **expected, 'plan_sha256': hashlib.sha256(plan.read_bytes()).hexdigest()}
            tf.verify_manifest(manifest, plan, expected)
            for key in expected:
                with self.subTest(field=key), self.assertRaises(ValueError):
                    tf.verify_manifest({**manifest, key: 'wrong'}, plan, expected)
            plan.write_bytes(b'replaced')
            with self.assertRaises(ValueError):
                tf.verify_manifest(manifest, plan, expected)
            plan.unlink()
            with self.assertRaises(ValueError):
                tf.verify_manifest(manifest, plan, expected)

    def test_project_path_cannot_escape_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'escape').symlink_to(root.parent, target_is_directory=True)
            for path in ['../other', str(root), 'escape', 'missing']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    tf.checked_project(path, root)


class OperationTests(unittest.TestCase):
    def test_plan_then_apply_uses_reviewed_binary_and_never_replans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'infra').mkdir()
            (root / 'bin').mkdir()
            executable = root / 'bin/terraform'
            executable.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['COMMAND_LOG'], 'a') as stream: stream.write(json.dumps(args) + '\\n')
if args[0] == 'version': print(json.dumps({'terraform_version': '1.12.2'}))
elif args[0] == 'plan':
    pathlib.Path(next(arg[5:] for arg in args if arg.startswith('-out='))).write_bytes(b'reviewed')
    sys.exit(int(os.environ.get('PLAN_EXIT_CODE', '2')))
''')
            executable.chmod(0o755)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
            sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            env = {**os.environ, 'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'], 'COMMAND_LOG': str(root / 'commands'), 'OPERATION': 'plan', 'PROJECT_DIR': 'infra', 'STACK_ID': 'production', 'TERRAFORM_VERSION': '1.12.2', 'TERRAMATE_VERSION': '', 'GITHUB_SHA': sha, 'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_RUN_ID': '11', 'RUNNER_TEMP': str(root), 'GITHUB_OUTPUT': str(root / 'output'), 'GITHUB_STEP_SUMMARY': str(root / 'summary'), 'PLAN_DIR': str(root / 'plan')}
            script = ROOT / '.github/actions/terraform-operation/operation.py'
            subprocess.run([sys.executable, str(script)], cwd=root, env=env, check=True, capture_output=True)
            self.assertIn('has-changes=true', (root / 'output').read_text())
            apply_env = {**env, 'OPERATION': 'apply', 'PLAN_SHA': sha, 'PLAN_RUN_ID': '11'}
            subprocess.run([sys.executable, str(script)], cwd=root, env=apply_env, check=True, capture_output=True)
            commands = [json.loads(line) for line in (root / 'commands').read_text().splitlines()]
            self.assertEqual(sum(args[0] == 'plan' for args in commands), 1)
            self.assertIn(['apply', '-input=false', str(root / 'plan/tfplan')], commands)
            (root / 'plan/tfplan').unlink()
            result = subprocess.run([sys.executable, str(script)], cwd=root, env=apply_env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            commands_after = [json.loads(line) for line in (root / 'commands').read_text().splitlines()]
            self.assertEqual(sum(args[0] == 'plan' for args in commands_after), 1)
            subprocess.run([sys.executable, str(script)], cwd=root, env={**env, 'OPERATION': 'drift', 'PLAN_EXIT_CODE': '0'}, check=True, capture_output=True)
            self.assertIn('has-changes=false', (root / 'output').read_text())
            result = subprocess.run([sys.executable, str(script)], cwd=root, env={**env, 'PLAN_EXIT_CODE': '1'}, capture_output=True)
            self.assertNotEqual(result.returncode, 0, 'Terraform errors must never count as no drift')


class PlanProtectionTests(unittest.TestCase):
    def test_encrypted_artifact_round_trip_and_tamper_rejection(self):
        from cryptography.exceptions import InvalidTag
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = 'ab' * 32
            (root / 'tfplan').write_bytes(b'private-provider-credentials')
            (root / 'manifest.json').write_text('{"version":1}')
            (root / 'plan.txt').write_text('private configuration')
            protection.protect('encrypt', root, key)
            self.assertEqual([path.name for path in root.iterdir()], ['plan.enc'])
            payload = (root / 'plan.enc').read_bytes()
            self.assertNotIn(b'private-provider-credentials', payload)
            with self.assertRaises(InvalidTag):
                protection.protect('decrypt', root, 'cd' * 32)
            protection.protect('decrypt', root, key)
            self.assertEqual((root / 'tfplan').read_bytes(), b'private-provider-credentials')
            (root / 'plan.enc').write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
            with self.assertRaises(InvalidTag):
                protection.protect('decrypt', root, key)

    def test_invalid_keys_and_archive_paths_are_rejected(self):
        import io
        import zipfile
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                protection.protect('encrypt', directory, 'invalid')
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr('../escape', 'untrusted')
            nonce = b'0' * 12
            key = 'ab' * 32
            payload = protection.HEADER + nonce + AESGCM(bytes.fromhex(key)).encrypt(nonce, buffer.getvalue(), protection.HEADER)
            (Path(directory) / 'plan.enc').write_bytes(payload)
            with self.assertRaises(ValueError):
                protection.protect('decrypt', directory, key)


class ImageTests(unittest.TestCase):
    digest = 'sha256:' + 'a' * 64

    def inspect(self, args):
        return {'digest': self.digest, 'manifests': [{'platform': {'os': 'linux', 'architecture': 'arm64'}}, {'platform': {'os': 'linux', 'architecture': 'amd64'}}]}

    def test_exact_digest_is_used_and_every_platform_selected(self):
        ref = 'registry.example/image@' + self.digest
        calls = []
        def inspect(args):
            calls.append(args)
            return self.inspect(args)
        result = images.resolve(ref, ['linux/arm64', 'linux/amd64'], inspect)
        self.assertEqual(len(result), 2)
        self.assertEqual(calls[0][4], ref)
        self.assertTrue(all(row['image'] == ref for row in result))

    def test_missing_platform_and_digest_mismatch_fail(self):
        with self.assertRaises(ValueError):
            images.resolve('image', ['linux/arm/v7'], self.inspect)
        with self.assertRaises(ValueError):
            images.resolve('image@sha256:' + 'b' * 64, ['linux/arm64'], self.inspect)

    def test_single_platform_manifest_is_supported(self):
        replies = iter([{'digest': self.digest}, {'os': 'linux', 'architecture': 'arm64'}])
        result = images.resolve('image:tag', ['linux/arm64'], lambda _: next(replies))
        self.assertEqual(result[0]['image'], 'image:tag@' + self.digest)

    def test_arm64_v8_manifest_matches_arm64_but_arm_v6_cannot_match_v7(self):
        def inspect(args):
            return {'digest': self.digest, 'manifests': [{'platform': {'os': 'linux', 'architecture': 'arm64', 'variant': 'v8'}}, {'platform': {'os': 'linux', 'architecture': 'arm', 'variant': 'v6'}}]}
        self.assertEqual(images.resolve('image', ['linux/arm64'], inspect)[0]['platform'], 'linux/arm64')
        with self.assertRaises(ValueError):
            images.resolve('image', ['linux/arm/v7'], inspect)

    def test_all_compose_service_images_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'compose.yaml').write_text('services: {}')
            def inspect(args):
                if args[1] == 'compose':
                    return {'services': {'db': {'image': 'postgres:16'}, 'app': {'image': 'app:1'}, 'worker': {'image': 'app:1'}}}
                return self.inspect(args)
            result = images.inventory('[]', '["compose.yaml"]', '.', 'linux/arm64', root, inspect)
            self.assertEqual(len(result), 2)
            self.assertTrue(any(row['image'].startswith('postgres:16@') for row in result))
            with self.assertRaises(ValueError):
                images.inventory('[]', '["../compose.yaml"]', '.', 'linux/arm64', root, inspect)

    def test_empty_invalid_and_oversized_inventories_fail(self):
        for value in ['{}', '["--help"]', json.dumps([f'image:{i}' for i in range(65)])]:
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                images.inventory(value, '[]', '.', 'linux/arm64', inspect=self.inspect)


class OCITests(unittest.TestCase):
    def test_credentials_are_isolated_private_and_removed(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = signing_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        with tempfile.TemporaryDirectory() as directory:
            env = {'RUNNER_TEMP': directory, 'GITHUB_ENV': str(Path(directory) / 'env')}
            cfg = {'user': 'ocid1.user.test', 'tenancy': 'ocid1.tenancy.test', 'fingerprint': 'ab:cd', 'region': 'eu-frankfurt-1'}
            passphrase = 'ab' * 32
            values = oci.configure(json.dumps(cfg), pem, passphrase, env)
            key = Path(values['OCI_PRIVATE_KEY_PATH'])
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertIn(b'BEGIN ENCRYPTED PRIVATE KEY', key.read_bytes())
            self.assertNotIn(pem, Path(env['GITHUB_ENV']).read_text())
            self.assertNotIn(passphrase, Path(env['GITHUB_ENV']).read_text())
            restored = serialization.load_pem_private_key(key.read_bytes(), password=passphrase.encode())
            self.assertEqual(restored.private_numbers(), signing_key.private_numbers())
            self.assertEqual(values['OCI_private_key_path'], values['OCI_PRIVATE_KEY_PATH'])
            oci.cleanup({**env, **values})
            self.assertFalse(key.exists())
            with self.assertRaises(ValueError):
                oci.configure(json.dumps({**cfg, 'region': 'bad\ninjected=true'}), pem, passphrase, env)


if __name__ == '__main__':
    unittest.main()
