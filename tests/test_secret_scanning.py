"""Exercise the shared scanner against leaks, templates, and deleted history."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.maintenance.check_workflows import ROOT, load


class SecretScanningTests(unittest.TestCase):
    def test_scanning_is_enabled_by_default(self):
        security = load(ROOT / '.github/workflows/security.yml')
        self.assertEqual(security['on']['workflow_call']['inputs']['enable-gitleaks']['default'], 'true')
        self.assertEqual(security['jobs']['secrets']['uses'], './.github/workflows/secret-scanning.yml')

    @unittest.skipUnless(shutil.which('gitleaks'), 'Gitleaks binary required for scanner integration')
    def test_database_test_values_tokens_and_deleted_history(self):
        workflow = load(ROOT / '.github/workflows/secret-scanning.yml')
        script = workflow['jobs']['scan']['steps'][-1]['run']
        policy = script.split("<<'TOML'\n", 1)[1].split('\nTOML', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'policy.toml'
            config.write_text(policy)
            repo = root / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            env = {**os.environ, 'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid', 'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}

            def commit():
                subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
                subprocess.run(['git', 'commit', '-qm', 'test: scanner fixtures'], cwd=repo, env=env, check=True)

            report = root / 'report.json'

            def scan():
                return subprocess.run(['gitleaks', 'git', '--config', str(config), '--redact=100', '--log-opts=HEAD', '--report-format=json', '--report-path', str(report), '.'], cwd=repo, capture_output=True, text=True)

            (repo / 'example.txt').write_text('DATABASE_URL=postgresql://user:your_password@localhost/test\nDATABASE_URL=postgresql://user:pass@host/db\nDATABASE_URL=postgresql+asyncpg://omnicare:omnicare_password@{DB_HOST}:5432/test\nDATABASE_URL=postgresql://omnicare:omnicare_password@localhost/omnicare_test\nDATABASE_URL=postgresql+asyncpg://${POSTGRES_USER:-omnicare}:${POSTGRES_PASSWORD}@db/test\n')
            commit()
            self.assertEqual(scan().returncode, 0)
            # A caller-owned policy must not disable centrally enforced detection.
            (repo / '.gitleaks.toml').write_text('title="empty caller policy"\n')
            test_values = ['s3nsitive' + 'DatabasePassword982']
            lines = [f'DATABASE_URL={scheme}://app:{test_values[0]}@db.example.invalid/app' for scheme in ('postgresql+asyncpg', 'mysql', 'mongodb+srv', 'rediss')]
            lines.append('DATABASE_URL=postgresql://app:' + 'password@db.service.net/app')
            lines.append('GITHUB_TOKEN=' + 'ghp_' + 'AbCdEf0123456789' * 2 + 'ABcd')
            (repo / 'leak.txt').write_text('\n'.join(lines))
            commit()
            (repo / 'leak.txt').unlink()
            commit()
            result = scan()
            self.assertEqual(result.returncode, 1, result.stderr)
            findings = json.loads(report.read_text())
            rules = [item['RuleID'] for item in findings]
            self.assertGreaterEqual(rules.count('database-connection-password'), 5)
            self.assertIn('github-pat', rules)
            self.assertNotIn(test_values[0], result.stdout + result.stderr + report.read_text())


@unittest.skipUnless(shutil.which('gitleaks'), 'Gitleaks binary required for scanner integration')
class PortablePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        workflow = load(ROOT / '.github/workflows/secret-scanning.yml')
        self.steps = workflow['jobs']['scan']['steps']
        policy = self.steps[-1]['run'].split("<<'TOML'\n", 1)[1].split('\nTOML', 1)[0]
        self.config = self.root / 'policy.toml'
        self.config.write_text(policy)

    def scan_value(self, value):
        return subprocess.run(
            ['gitleaks', 'stdin', '--config', str(self.config), '--redact=100', '--verbose'],
            input=value, capture_output=True, text=True,
        )

    def test_scanner_source_and_synthetic_regressions_are_not_findings(self):
        for path in (ROOT / '.github/workflows/secret-scanning.yml', Path(__file__)):
            with self.subTest(path=path.name):
                result = self.scan_value(path.read_text())
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_project_independent_database_templates(self):
        for scheme, user, value, host in [
            ('postgresql', 'trading', 'trading_password', 'localhost'),
            ('postgresql+asyncpg', 'catalog', 'catalog_test', '127.0.0.2'),
            ('mysql', 'shop', 'shop_dev', 'mysql:3306'),
            ('mongodb+srv', 'demo', 'demo', 'mongodb'),
            ('redis', 'cache', 'cache_local', '[::1]:6379'),
            ('postgresql', 'ci', 'ci_test_only', '{SERVICE_HOST}'),
            ('postgresql', 'sample', 'pass', 'db.example.invalid'),
            ('postgresql', '${APP_USER:-app}', '${APP_PASSWORD}', 'db'),
        ]:
            with self.subTest(scheme=scheme, host=host):
                url = f'DATABASE_URL={scheme}://{user}:{value}@{host}/app'
                result = self.scan_value(url)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('omnicare', self.config.read_text())
        result = self.scan_value('DATABASE_URL=postgresql://app:${APP_PASSWORD:-EmbeddedSecret982Ab}@localhost/app')  # gitleaks:allow -- deliberately synthetic fallback credential
        self.assertEqual(result.returncode, 1, 'A variable with an embedded credential is not a safe template')

    def test_real_database_credentials_are_not_hidden_by_paths_or_hosts(self):
        value = 'Synthetic' + 'Credential982Ab'
        for host in ('localhost', 'db', '[::1]:5432', 'db.service.net', 'db.example.invalid', 'localhost.evil.net'):
            with self.subTest(host=host):
                url = f'DATABASE_URL=postgresql://app:{value}@{host}/app'
                result = self.scan_value(url)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('database-connection-password', result.stdout + result.stderr)
                self.assertNotIn(value, result.stdout + result.stderr)
        for value in ('password', 'catalog_test', '${APP_PASSWORD:-EmbeddedSecret982Ab}'):
            result = self.scan_value(f'DATABASE_URL=postgresql://app:{value}@db.service.net/app')
            self.assertEqual(result.returncode, 1, result.stderr)

    def test_synthetic_markers_and_public_addresses_are_rule_scoped(self):
        values = ['example-fixture-value-123456', 'idemp-cancel-01', '0x' + '1234abcdef' * 4]
        for value in values:
            with self.subTest(kind=value[:8]):
                result = self.scan_value(f'API_KEY="{value}"')
                self.assertEqual(result.returncode, 0, result.stderr)
        result = self.scan_value('API_KEY="test-' + 'AbCdEf0123456789XyZ' + '"')
        self.assertEqual(result.returncode, 1, 'A test-prefixed API key can still be a real credential')
        value = 'ghp_' + 'AbCdEf0123456789' * 2 + 'ABcd'
        result = self.scan_value(f'EXAMPLE_TOKEN="{value}"')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('github-pat', result.stdout + result.stderr)
        self.assertNotIn(value, result.stdout + result.stderr)

    def test_scan_range_selection_and_history_leaks(self):
        repo = self.root / 'repo'
        repo.mkdir()
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid', 'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}

        def git(*args):
            return subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()

        def commit():
            git('add', '.')
            git('commit', '-qm', 'test: scope fixtures')
            return git('rev-parse', 'HEAD')

        git('init', '-q')
        fixture = 'Synthetic' + 'Credential982Ab'
        (repo / 'old.txt').write_text(f'DATABASE_URL=postgresql://app:{fixture}@db.service.net/app')
        commit()
        (repo / 'safe.txt').write_text('safe base')
        base = commit()
        (repo / 'safe.txt').write_text('safe change')
        head = commit()
        step = next(step for step in self.steps if step.get('id') == 'scope')
        program = step['run'].split("<<'PYCODE'\n", 1)[1].rsplit('\nPYCODE', 1)[0]

        def scope(kind, event):
            event_file, output = self.root / 'event.json', self.root / 'output'
            event_file.write_text(json.dumps(event))
            output.unlink(missing_ok=True)
            result = subprocess.run([os.sys.executable, '-c', program], cwd=repo,
                env={**env, 'SCAN_EVENT_NAME': kind, 'GITHUB_EVENT_PATH': str(event_file), 'GITHUB_OUTPUT': str(output)},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return output.read_text().strip().removeprefix('revision=')

        events = [
            ('pull_request', {'pull_request': {'base': {'sha': base}, 'head': {'sha': head}}}),
            ('push', {'before': base, 'after': head}),
            ('merge_group', {'merge_group': {'base_sha': base, 'head_sha': head}}),
        ]
        for kind, event in events:
            self.assertEqual(scope(kind, event), base + '..' + head)
        for kind, event in [('schedule', {}), ('workflow_dispatch', {}), ('push', {'before': '0' * 40, 'after': head}), ('push', {'before': 'f' * 40, 'after': head}), ('push', {'before': '--all', 'after': head})]:
            self.assertEqual(scope(kind, event), 'HEAD')

        def scan(revision):
            return subprocess.run(['gitleaks', 'git', '--config', str(self.config), '--redact=100', '--log-opts=' + revision, '.'], cwd=repo, capture_output=True, text=True)

        self.assertEqual(scan(base + '..' + head).returncode, 0)
        self.assertEqual(scan('HEAD').returncode, 1)
        (repo / 'new.txt').write_text(f'DATABASE_URL=postgresql://app:{fixture}@db.service.net/app')
        commit()
        (repo / 'new.txt').unlink()
        latest = commit()
        self.assertEqual(scan(base + '..' + latest).returncode, 1, 'Deleted leaks within the change must still fail')
