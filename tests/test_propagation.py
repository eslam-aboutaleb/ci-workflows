"""Publishing bot registry and downstream PR description regressions."""
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from scripts.maintenance import propagate_updates as bot


class RegistryTests(unittest.TestCase):
    def load(self, value):
        with patch.dict(os.environ, {bot.DOWNSTREAM_SECRET: value}):
            return bot.load_downstream()

    def test_secret_registry_accepts_strings_and_objects(self):
        value = {'version': 1, 'repositories': ['owner/first', {'repo': 'owner/second'}]}
        self.assertEqual(self.load(json.dumps(value)), [{'repo': 'owner/first'}, {'repo': 'owner/second'}])

    def test_invalid_registry_fails_without_disclosing_secret(self):
        invalid = ['', 'PRIVATE_INVALID_JSON', '[]', '{"version":2}',
                   '{"version":1,"repositories":[]}',
                   '{"version":1,"repositories":["private/secret","PRIVATE_INVALID_SLUG"]}',
                   '{"version":1,"repositories":["owner/repo","OWNER/REPO"]}']
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                self.load(value)
            self.assertNotIn('PRIVATE_', str(error.exception))
            self.assertNotIn('private/secret', str(error.exception))

    def test_missing_registry_cannot_publish(self):
        with patch.dict(os.environ, {bot.DOWNSTREAM_SECRET: ''}), \
             patch('sys.argv', ['propagate', '--target-sha', 'a' * 40]), \
             patch.object(bot, 'propagate_to_repo') as publish:
            with self.assertRaisesRegex(ValueError, 'required'):
                bot.main()
            publish.assert_not_called()

    def test_explicit_repository_override_and_dry_run(self):
        with patch.dict(os.environ, {bot.DOWNSTREAM_SECRET: '', 'GITHUB_STEP_SUMMARY': ''}), \
             patch('sys.argv', ['propagate', '--target-sha', 'a' * 40, '--repo', 'owner/override', '--dry-run']), \
             patch.object(bot, 'propagate_to_repo', return_value={}) as publish, \
             contextlib.redirect_stdout(io.StringIO()):
            bot.main()
            publish.assert_called_once_with('owner/override', 'a' * 40, dry_run=True)


class DescriptionTests(unittest.TestCase):
    def test_description_satisfies_required_sections_and_reports_pending_checks(self):
        body = bot.format_pr_body('a' * 40, {'.github/workflows/ci.yml': [('python.yml', 'b' * 40)]})
        for heading in ['Description', 'Type of Change', 'Behavior and Compatibility',
                        'Verification Evidence', 'Independent Review', 'Checklist']:
            self.assertIn(f'## {heading}\n', body)
        self.assertEqual(body.count('- [x] CI/CD or tooling enhancement'), 1)
        self.assertIn('tests/ and workflow validation, is pending', body)
        self.assertIn('were not run by the publishing bot', body)
        self.assertIn('Pending maintainer review', body)
        self.assertIn(f'compare/{"b" * 40}...{"a" * 40}', body)
        self.assertIn('| `.github/workflows/ci.yml` | `python.yml` |', body)
        self.assertNotIn('- [x] My code adheres', body)


if __name__ == '__main__':
    unittest.main()
