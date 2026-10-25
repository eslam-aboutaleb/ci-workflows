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
    def test_database_passwords_tokens_and_deleted_history(self):
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
                return subprocess.run(['gitleaks', 'git', '--config', str(config), '--redact=100', '--log-opts=--all', '--report-format=json', '--report-path', str(report), '.'], cwd=repo, capture_output=True, text=True)

            (repo / 'example.txt').write_text('DATABASE_URL=postgresql://user:your_password@localhost/test\nDATABASE_URL=postgresql://omnicare:omnicare_password@localhost/omnicare_test\nDATABASE_URL=postgresql+asyncpg://${POSTGRES_USER:-omnicare}:${POSTGRES_PASSWORD}@db/test\n')
            commit()
            self.assertEqual(scan().returncode, 0)
            # A caller-owned policy must not disable centrally enforced detection.
            (repo / '.gitleaks.toml').write_text('title="empty caller policy"\n')
            passwords = ['s3nsitive' + 'DatabasePassword982']
            lines = [f'DATABASE_URL={scheme}://app:{passwords[0]}@db.example.invalid/app' for scheme in ('postgresql+asyncpg', 'mysql', 'mongodb+srv', 'rediss')]
            lines.append('GITHUB_TOKEN=' + 'ghp_' + 'AbCdEf0123456789' * 2 + 'ABcd')
            (repo / 'leak.txt').write_text('\n'.join(lines))
            commit()
            (repo / 'leak.txt').unlink()
            commit()
            result = scan()
            self.assertEqual(result.returncode, 1, result.stderr)
            findings = json.loads(report.read_text())
            rules = [item['RuleID'] for item in findings]
            self.assertGreaterEqual(rules.count('database-connection-password'), 4)
            self.assertIn('github-pat', rules)
            self.assertNotIn(passwords[0], result.stdout + result.stderr + report.read_text())
