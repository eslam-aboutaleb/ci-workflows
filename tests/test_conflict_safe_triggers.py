"""Regression tests for event triggers that honor GitHub PR conflict suppression."""
import unittest

from scripts.maintenance.check_workflows import check_conflict_safe_triggers


class ConflictSafeTriggerTests(unittest.TestCase):
    def test_pr_and_main_push_are_accepted(self):
        check_conflict_safe_triggers({'on': {'pull_request': None, 'push': {'branches': ['main']}}}, 'caller')

    def test_reusable_and_manual_workflows_are_accepted(self):
        check_conflict_safe_triggers({'on': {'workflow_call': None, 'workflow_dispatch': None}}, 'caller')

    def test_branch_push_bypasses_are_rejected(self):
        for push in [None, {}, {'branches': ['**']}, {'branches-ignore': ['main']},
                     {'branches': ['main', 'feature/**']}, {'branches': ['main'], 'tags': ['v*']}]:
            with self.subTest(push=push), self.assertRaises(AssertionError):
                check_conflict_safe_triggers({'on': {'pull_request': None, 'push': push}}, 'caller')

    def test_pull_request_target_is_rejected(self):
        with self.assertRaises(AssertionError):
            check_conflict_safe_triggers({'on': {'pull_request_target': None}}, 'caller')
