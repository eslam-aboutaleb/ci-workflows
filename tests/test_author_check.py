"""Regression tests for the commit author identity check in the shared CI."""
import contextlib
import hashlib
import io
import json
import os
import unittest
from unittest import mock

from scripts.maintenance.check_workflows import ROOT, load

BLOCKED_EMAIL = "someone.private@example.org"


class AuthorCheckTests(unittest.TestCase):
    def setUp(self):
        workflow = load(ROOT / ".github/workflows/author-check.yml")
        step = next(s for s in workflow["jobs"]["check"]["steps"] if "run" in s)
        self.program = step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        self.responses = {}
        self.env = {
            "GH_TOKEN": "test-token",
            "GH_API_URL": "https://api.github.test",
            "GH_REPOSITORY": "owner/app",
            "EVENT_NAME": "pull_request",
            "PR_NUMBER": "7",
            "PUSH_BEFORE": "a" * 40,
            "PUSH_AFTER": "b" * 40,
            "EXTRA_PATTERNS": "",
            "BLOCKED_EMAIL_SHA256": hashlib.sha256(BLOCKED_EMAIL.encode()).hexdigest(),
        }

    @staticmethod
    def commit(index=1, author=("Real Person", "real@example.org"), committer=None, message="feat: x"):
        committer = committer or author
        return {
            "sha": f"{index:040x}",
            "commit": {
                "author": {"name": author[0], "email": author[1]},
                "committer": {"name": committer[0], "email": committer[1]},
                "message": message,
            },
        }

    def urlopen(self, request, timeout=0):
        url = request.full_url.split("/repos/owner/app/", 1)[1]
        if url not in self.responses:
            raise AssertionError(f"Unexpected API request: {url}")
        return io.BytesIO(json.dumps(self.responses[url]).encode("utf-8"))

    def pr(self, commits):
        self.responses["pulls/7"] = {"commits": len(commits)}
        self.responses["pulls/7/commits?per_page=100&page=1"] = commits

    def run_check(self, **changes):
        output = io.StringIO()
        code = 0
        with mock.patch.dict(os.environ, {**self.env, **changes}, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=self.urlopen):
                with contextlib.redirect_stdout(output):
                    try:
                        exec(self.program, {"__name__": "__main__"})
                    except SystemExit as error:
                        code = error.code or 0
        return code, output.getvalue()

    def test_real_people_and_bots_pass(self):
        self.pr([
            self.commit(1),
            self.commit(2, author=("Claude Dupont", "claude.dupont@example.fr")),
            self.commit(3, author=("Devin Smith", "devin@example.org")),
            self.commit(4, author=("Gemini Rossi", "g.rossi@example.it")),
            self.commit(5, author=("Eslam Ehab", "eslam-aboutaleb@users.noreply.github.com"),
                        committer=("GitHub", "noreply@github.com")),
            self.commit(6, author=("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
                        committer=("GitHub", "noreply@github.com")),
            self.commit(7, author=("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")),
            self.commit(8, message="feat: y\n\nCo-authored-by: Real Person <real@example.org>"),
        ])
        code, output = self.run_check()
        self.assertEqual(code, 0, output)

    def test_rejects_agent_authors_and_committers(self):
        agents = [
            ("Claude", "noreply@anthropic.com"),
            ("Claude Code", "x@example.org"),
            ("Codex", "codex@example.org"),
            ("OpenAI Codex", "noreply@openai.com"),
            ("Instinct Agent", "agent@instinct.com"),
            ("Instinct", "instinct@users.noreply.github.com"),
            ("KiloCode", "kilo@local"),
            ("Copilot", "198982749+Copilot@users.noreply.github.com"),
            ("copilot-swe-agent[bot]", "x@users.noreply.github.com"),
            ("Cursor Agent", "cursoragent@cursor.com"),
            ("Eslam (aider)", "e@example.org"),
            ("Gemini CLI", "g@example.org"),
            ("devin-ai-integration[bot]", "158243242+devin-ai-integration[bot]@users.noreply.github.com"),
            ("Windsurf", "w@example.org"),
        ]
        for agent in agents:
            for role in ("author", "committer"):
                with self.subTest(agent=agent, role=role):
                    real = ("Real Person", "real@example.org")
                    commit = self.commit(1, author=agent if role == "author" else real,
                                         committer=agent if role == "committer" else real)
                    self.pr([commit])
                    code, output = self.run_check()
                    self.assertNotEqual(code, 0)
                    self.assertIn("::error::", output)
                    self.assertIn(role, output)

    def test_rejects_agent_co_author_trailers(self):
        for trailer in (
            "Co-authored-by: Claude <noreply@anthropic.com>",
            "co-authored-by: Instinct assistant <x@users.noreply.github.com>",
            "Co-authored-by: Someone <someone@example.org>\nCo-authored-by: Codex <c@example.org>",
        ):
            with self.subTest(trailer=trailer):
                self.pr([self.commit(1, message=f"feat: x\n\n{trailer}")])
                code, output = self.run_check()
                self.assertNotEqual(code, 0)
                self.assertIn("Co-authored-by", output)

    def test_rejects_blocked_email_hash_without_printing_it(self):
        for email in (BLOCKED_EMAIL, BLOCKED_EMAIL.upper()):
            self.pr([self.commit(1, author=("Eslam", email))])
            code, output = self.run_check()
            self.assertNotEqual(code, 0)
            self.assertNotIn(email, output)

    def test_default_hashes_cover_the_two_personal_addresses(self):
        workflow = load(ROOT / ".github/workflows/author-check.yml")
        default = workflow["on"]["workflow_call"]["inputs"]["blocked-email-sha256"]["default"]
        self.assertEqual(len(default.split(",")), 2)

    def test_extra_patterns_are_configurable_and_invalid_ones_fail(self):
        self.pr([self.commit(1, author=("Some Bot", "x@example.org"))])
        self.assertEqual(self.run_check()[0], 0)
        self.assertNotEqual(self.run_check(EXTRA_PATTERNS="# comment\nsome bot")[0], 0)
        self.assertNotEqual(self.run_check(EXTRA_PATTERNS="(")[0], 0)

    def test_checks_push_range_and_fails_closed_when_truncated(self):
        before, after = "a" * 40, "b" * 40
        url = f"compare/{before}...{after}?per_page=100&page=1"
        self.responses[url] = {"total_commits": 1, "commits": [self.commit(1, author=("Codex", "c@example.org"))]}
        self.assertNotEqual(self.run_check(EVENT_NAME="push")[0], 0)
        self.responses[url] = {"total_commits": 251, "commits": []}
        code, output = self.run_check(EVENT_NAME="push")
        self.assertNotEqual(code, 0)
        self.assertIn("250", output)

    def test_unrelated_events_are_skipped(self):
        self.assertEqual(self.run_check(EVENT_NAME="workflow_dispatch")[0], 0)


if __name__ == "__main__":
    unittest.main()
