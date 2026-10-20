"""Exercise the actual embedded discovery and quality-gate programs with repository fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_workflows import ROOT, load

CI = load(ROOT / '.github/workflows/ci.yml')


def embedded(job, step_id=None):
    steps = CI['jobs'][job]['steps']
    step = next(s for s in steps if s.get('id') == step_id) if step_id else steps[0]
    return step['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]


DISCOVERY = embedded('discover', 'scan')
GATE = embedded('quality-gate')


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.output = self.repo / '.git-ci-output'
        self.summary = self.repo / '.git-ci-summary'

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def config(self, **values):
        self.write('.github/ci.json', json.dumps({'version': 1, **values}))

    def node(self, path='frontend', manager='npm', **package):
        scripts = {name: 'echo fixture' for name in ('lint', 'typecheck', 'test', 'build')}
        scripts.update(package.get('scripts', {}))
        package = {**package, 'scripts': scripts}
        self.write(f'{path}/package.json', json.dumps(package))
        self.write(f'{path}/' + {'npm': 'package-lock.json', 'pnpm': 'pnpm-lock.yaml', 'bun': 'bun.lock'}[manager], '{}')

    def run_discovery(self, success=True, **env):
        self.output.unlink(missing_ok=True)
        result = subprocess.run([sys.executable, '-c', DISCOVERY], cwd=self.repo, env={**os.environ, 'GITHUB_OUTPUT': str(self.output), 'GITHUB_STEP_SUMMARY': str(self.summary), **env}, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return {key: json.loads(value) for key, value in (line.split('=', 1) for line in self.output.read_text().splitlines())}
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.output.exists(), 'Discovery must not emit partial plans after a validation failure')
        return result.stderr

    def git(self, *arguments):
        result = subprocess.run(['git', *arguments], cwd=self.repo, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=CI fixture', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def test_fullstack_discovers_all_checks(self):
        self.write('backend/pyproject.toml', '[project]\nname="api"\nversion="1.0.0"\n[build-system]\nrequires=[]\n')
        self.write('backend/uv.lock', '')
        self.node('frontend', scripts={'test:e2e': 'playwright test', 'test:visual': 'playwright test -c visual.config.ts'})
        self.write('backend/Dockerfile', 'FROM scratch\n')
        self.write('frontend/Dockerfile.production', 'FROM scratch\n')
        self.write('tools/dev.Dockerfile', 'FROM scratch\n')
        self.write('docs/Makefile', 'ci-install:\nci-docs:\nci-docs-links:\nci-generated:\n')
        self.write('backend/Makefile', 'ci-install:\nci-migrate:\nci-integration:\nci-eval:\n')
        result = self.run_discovery()
        self.assertEqual(len(result['python-projects']), 1)
        self.assertTrue(result['python-projects'][0]['build_distribution'])
        self.assertEqual(result['python-projects'][0]['manager'], 'uv')
        self.assertEqual(len(result['node-projects']), 1)
        self.assertEqual(len(result['playwright-projects']), 2)
        self.assertEqual(len(result['dockerfiles']), 3)
        self.assertEqual(len(result['project-checks']), 3)
        self.assertTrue(next(p for p in result['project-checks'] if p['target'] == 'ci-generated')['check_drift'])
        self.assertEqual(len(result['integration-projects']), 1)
        self.assertEqual(len(result['ai-evaluation-projects']), 1)
        self.assertEqual(result['pypi-projects'], [], 'Publishing must be explicitly enabled')

    def test_malformed_manifests_fail(self):
        for path, content in [('backend/pyproject.toml', '[broken'), ('frontend/package.json', '{broken')]:
            with self.subTest(path=path):
                self.write(path, content)
                self.run_discovery(success=False)
                (self.repo / path).unlink()

    def test_unknown_and_duplicate_configuration_keys_fail(self):
        for content in ['{"version":1,"typo":{}}', '{"version":1,"version":1}', '{"version":true}']:
            with self.subTest(content=content):
                self.write('.github/ci.json', content)
                self.run_discovery(success=False)

    def test_standalone_package_cannot_skip_missing_lockfile(self):
        self.write('nested/package.json', '{}')
        self.assertIn('lockfile', self.run_discovery(success=False))

    def test_workspace_root_covers_members(self):
        self.node('.', workspaces=['packages/*'])
        self.write('packages/library/package.json', '{}')
        result = self.run_discovery()
        self.assertEqual([p['project_dir'] for p in result['node-projects']], ['.'])
        self.assertIn('packages/library -> .', self.summary.read_text())

    def test_pnpm_workspace_exclusions_are_respected(self):
        self.node('.', 'pnpm')
        self.write('pnpm-workspace.yaml', "packages:\n  - 'packages/*'\n  - '!packages/private'\n")
        self.write('packages/library/package.json', '{}')
        self.assertEqual(len(self.run_discovery()['node-projects']), 1)
        self.write('packages/private/package.json', '{}')
        self.assertIn('lockfile', self.run_discovery(success=False))

    def test_nonmember_with_no_lockfile_is_not_silently_ignored(self):
        self.node('.', workspaces=['packages/*'])
        self.write('independent/package.json', '{}')
        self.run_discovery(success=False)

    def test_conflicting_lockfiles_fail(self):
        self.node()
        self.write('frontend/pnpm-lock.yaml', '')
        self.assertIn('Conflicting', self.run_discovery(success=False))

    def test_bun_is_discovered(self):
        self.node('frontend', 'bun', packageManager='bun@1.3.9')
        self.assertEqual(self.run_discovery()['node-projects'][0]['manager'], 'bun')

    def test_per_project_coverage_context_and_publish_settings(self):
        self.write('backend/pyproject.toml', '[project]\nname="api"\nversion="1.0.0"\n')
        self.write('backend/Dockerfile', 'FROM scratch\n')
        self.config(python={'backend': {'coverage-source': 'api', 'coverage-min': 85, 'publish-pypi': True}}, docker={'backend/Dockerfile': {'build-context': 'backend', 'platforms': 'linux/amd64,linux/arm64', 'build-args': ['MODE=production']}})
        result = self.run_discovery()
        self.assertEqual(result['python-projects'][0]['coverage_min'], 85)
        self.assertFalse(result['python-projects'][0]['build_distribution'])
        self.assertEqual(result['dockerfiles'][0]['build_context'], 'backend')
        self.assertEqual(result['dockerfiles'][0]['build_args'], 'MODE=production')
        self.assertEqual(result['pypi-projects'][0]['distribution_name'], 'api')

    def test_misspelled_project_and_invalid_coverage_fail(self):
        self.write('backend/pyproject.toml', '[project]\nname="api"\n')
        for values in [{'backed': {}}, {'backend': {'coverage-min': 80}}, {'backend': {'python-versions': ['3.12', '3.12']}}]:
            self.config(python=values)
            self.run_discovery(success=False)

    def test_other_ecosystems_require_quality_contract(self):
        self.write('service/Cargo.toml', '')
        self.assertIn('ci-quality', self.run_discovery(success=False))
        self.write('Makefile', 'ci-install:\nci-quality:\n')
        self.assertEqual(self.run_discovery()['project-checks'][0]['target'], 'ci-quality')

    def test_docs_contract_requires_installer(self):
        self.write('docs/Makefile', 'ci-docs:\n')
        self.assertIn('ci-install', self.run_discovery(success=False))

    def test_paths_cannot_escape_repository(self):
        self.write('Dockerfile', 'FROM scratch\n')
        self.config(docker={'Dockerfile': {'build-context': '../'}})
        self.run_discovery(success=False)

    def test_adoption_example_produces_a_valid_plan(self):
        self.write('.github/ci.json', (ROOT / '.github/examples/ci.json').read_text())
        self.write('backend/pyproject.toml', '[project]\nname="api"\nversion="1.0.0"\n')
        self.node('frontend', scripts={'test:e2e': 'playwright test'})
        self.write('backend/Dockerfile', 'FROM scratch\n')
        self.write('frontend/Dockerfile', 'FROM scratch\n')
        self.write('docs/Makefile', 'ci-install:\nci-docs:\n')
        self.write('Makefile', 'ci-install:\nci-generated:\n')
        result = self.run_discovery()
        self.assertEqual(result['python-projects'][0]['coverage_min'], 85)
        self.assertEqual(result['playwright-projects'][0]['shards'], 2)
        self.assertEqual(len(result['project-checks']), 2)

    def setup_changed_projects(self):
        self.node('frontend')
        self.node('backend')
        self.write('frontend/src/app.js', 'old')
        self.write('backend/src/app.js', 'old')
        self.config(node={'frontend': {'paths': ['frontend/**']}, 'backend': {'paths': ['backend/**']}})
        self.git('init', '-q')
        return self.commit()

    def test_change_filter_is_explicit_and_only_runs_for_pull_requests(self):
        base = self.setup_changed_projects()
        self.write('frontend/src/app.js', 'changed')
        self.commit()
        env = {'CHANGES_ONLY': 'true', 'EVENT_NAME': 'pull_request', 'BASE_SHA': base}
        self.assertEqual([p['project_dir'] for p in self.run_discovery(**env)['node-projects']], ['frontend'])
        self.assertEqual(len(self.run_discovery(**{**env, 'EVENT_NAME': 'workflow_dispatch'})['node-projects']), 2)
        self.assertEqual(len(self.run_discovery(**{**env, 'CHANGES_ONLY': 'false'})['node-projects']), 2)
        self.assertEqual(len(self.run_discovery(**{**env, 'BASE_SHA': 'a' * 40})['node-projects']), 2)

    def test_root_and_workflow_changes_invalidate_filters(self):
        base = self.setup_changed_projects()
        self.write('.github/workflows/caller.yml', 'name: CI')
        self.commit()
        result = self.run_discovery(CHANGES_ONLY='true', EVENT_NAME='pull_request', BASE_SHA=base)
        self.assertEqual(len(result['node-projects']), 2)
        self.assertTrue(result['workflow-files'])

    def test_dependency_directories_are_excluded(self):
        self.write('node_modules/untrusted/package.json', 'broken')
        self.write('.venv/pyproject.toml', 'broken')
        self.assertEqual(self.run_discovery()['node-projects'], [])

    def test_node_and_browser_prepare_real_shard_matrices(self):
        self.node('frontend', 'bun', packageManager='bun@1.3.9', scripts={'test:visual': 'playwright test'}, devDependencies={'@playwright/test': '1.58.0'})
        def prepare(filename, env):
            workflow = load(ROOT / '.github/workflows' / filename)
            step = next(step for step in workflow['jobs']['validate']['steps'] if step.get('id') == 'contract')
            program = step['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
            self.output.unlink(missing_ok=True)
            return subprocess.run([sys.executable, '-c', program], cwd=self.repo, env={**os.environ, 'PROJECT_DIR': 'frontend', 'MANAGER': 'bun', 'BUN_VERSION': '', 'GITHUB_OUTPUT': str(self.output), **env}, capture_output=True, text=True)
        node_env = {'NODE_VERSIONS': '["22","24"]', 'OPERATING_SYSTEMS': '["ubuntu-latest","windows-latest"]', 'TEST_SHARDS': '2', 'AUDIT_LEVEL': 'high'}
        result = prepare('node.yml', node_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('shards=[1, 2]', self.output.read_text())
        self.assertIn('bun-version=1.3.9', self.output.read_text())
        self.assertNotEqual(prepare('node.yml', {**node_env, 'TEST_SHARDS': '2.5'}).returncode, 0)
        browser_env = {'TEST_SCRIPT': 'test:visual', 'BROWSERS': 'chromium firefox', 'SHARDS': '3'}
        result = prepare('browser.yml', browser_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('shards=[1, 2, 3]', self.output.read_text())
        self.assertNotEqual(prepare('browser.yml', {**browser_env, 'BROWSERS': 'unknown'}).returncode, 0)

    def test_generated_drift_detects_modified_and_new_files(self):
        self.git('init', '-q')
        self.write('generated.txt', 'reviewed output')
        self.commit()
        workflow = load(ROOT / '.github/workflows/project-check.yml')
        script = next(step['run'] for step in workflow['jobs']['check']['steps'] if step.get('name') == 'Check generated files for drift')
        def drift():
            return subprocess.run(['bash', '-c', script], cwd=self.repo, capture_output=True, text=True).returncode
        self.assertEqual(drift(), 0)
        self.write('generated.txt', 'changed output')
        self.assertNotEqual(drift(), 0)
        self.git('checkout', '--', 'generated.txt')
        self.write('new-generated.txt', 'new output')
        self.assertNotEqual(drift(), 0)

    def test_docker_preflight_resolves_context_relative_to_project(self):
        self.write('backend/docker/Dockerfile', 'FROM scratch\n')
        workflow = load(ROOT / '.github/workflows/docker.yml')
        step = next(step for step in workflow['jobs']['docker-build']['steps'] if step.get('id') == 'contract')
        program = step['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        env = {**os.environ, 'PROJECT_DIR': 'backend', 'DOCKERFILE': 'docker/Dockerfile', 'BUILD_CONTEXT': '.', 'PLATFORMS': 'linux/amd64,linux/arm64', 'TARGET': '', 'BUILD_ARGS': 'MODE=production', 'GITHUB_OUTPUT': str(self.output)}
        result = subprocess.run([sys.executable, '-c', program], cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dockerfile=backend/docker/Dockerfile', self.output.read_text())
        self.assertIn('context=backend', self.output.read_text())
        result = subprocess.run([sys.executable, '-c', program], cwd=self.repo, env={**env, 'BUILD_CONTEXT': '..'}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


class QualityGateTests(unittest.TestCase):
    def gate(self, overrides=None, outputs=None, trusted=False, event='pull_request'):
        needs = {name: {'result': 'skipped'} for name in CI['jobs']['quality-gate']['needs']}
        needs['discover'] = {'result': 'success', 'outputs': outputs or {}}
        needs['security'] = {'result': 'success'}
        needs.update(overrides or {})
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', GATE], env={**os.environ, 'NEEDS_JSON': json.dumps(needs), 'GITHUB_STEP_SUMMARY': str(Path(directory) / 'summary'), 'EVENT_NAME': event, 'TRUSTED_AI_EVENT': str(trusted).lower()}, capture_output=True, text=True)
            return result.returncode

    def test_unmatched_optional_checks_can_skip(self):
        self.assertEqual(self.gate(), 0)

    def test_selected_check_cannot_skip(self):
        self.assertNotEqual(self.gate(outputs={'python-projects': '[{"project_dir":"backend"}]'}), 0)
        self.assertEqual(self.gate({'python': {'result': 'success'}}, outputs={'python-projects': '[{"project_dir":"backend"}]'}), 0)

    def test_failed_or_cancelled_dependencies_fail_gate(self):
        for job in CI['jobs']['quality-gate']['needs']:
            for result in ('failure', 'cancelled'):
                with self.subTest(job=job, result=result):
                    self.assertNotEqual(self.gate({job: {'result': result}}), 0)

    def test_provider_evaluations_are_expected_only_on_trusted_runs(self):
        outputs = {'ai-evaluation-projects': '[{"project_dir":"."}]'}
        self.assertEqual(self.gate(outputs=outputs), 0)
        self.assertNotEqual(self.gate(outputs=outputs, event='push', trusted=True), 0)


if __name__ == '__main__':
    unittest.main()
