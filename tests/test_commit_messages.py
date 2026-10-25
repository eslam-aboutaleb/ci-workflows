"""Regression tests for semantic commit-message enforcement in the shared CI."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

from scripts.maintenance.check_workflows import ROOT, load


class CommitMessageTests(unittest.TestCase):
    def setUp(self):
        workflow = load(ROOT / ".github/workflows/ci.yml")
        self.assertIn("commit-messages", workflow["jobs"])
        self.assertIn("commit-messages", workflow["jobs"]["quality-gate"]["needs"])
        job = workflow["jobs"]["commit-messages"]
        self.assertEqual(job["uses"], "./.github/workflows/commit-messages.yml")
        self.assertEqual(job["permissions"]["pull-requests"], "read")
        reusable = load(ROOT / ".github/workflows/commit-messages.yml")
        step = next(s for s in reusable["jobs"]["check"]["steps"] if "run" in s)
        self.program = step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        self.responses = {}
        self.visited = []
        self.before = "a" * 40
        self.after = "b" * 40
        self.env = {
            "GH_TOKEN": "test-token",
            "GH_API_URL": "https://api.github.test",
            "GH_REPOSITORY": "owner/app",
            "EVENT_NAME": "pull_request",
            "PR_NUMBER": "19",
            "PUSH_BEFORE": self.before,
            "PUSH_AFTER": self.after,
        }

    @staticmethod
    def commit(message, index=1, parents=1):
        return {
            "sha": f"{index:040x}",
            "commit": {"message": message},
            "parents": [{"sha": str(p)} for p in range(parents)],
        }

    def urlopen(self, request, timeout=0):
        self.assertGreater(timeout, 0)
        self.assertIn("Bearer test-token", request.get_header("Authorization"))
        url = request.full_url.split("/repos/owner/app/", 1)[1]
        self.visited.append(url)
        if url not in self.responses:
            raise AssertionError(f"Unexpected API request: {url}")
        return io.BytesIO(json.dumps(self.responses[url]).encode("utf-8"))

    def pr(self, messages):
        self.responses["pulls/19"] = {"commits": len(messages)}
        for page, index in enumerate(range(0, len(messages), 100), 1):
            self.responses[f"pulls/19/commits?per_page=100&page={page}"] = [
                self.commit(message, index + offset + 1)
                for offset, message in enumerate(messages[index:index + 100])
            ]

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

    def test_accepts_the_seven_types_and_optional_scope(self):
        self.pr([
            "chore: update tooling",
            "docs: explain setup",
            "feat(auth): add login\n\nCo-authored-by: Example <example@example.org>",
            "fix: resolve regression",
            "refactor(api): simplify handler",
            "style: apply formatter",
            "test(parser): add edge cases",
        ])
        self.assertEqual(self.run_check()[0], 0)

    def test_rejects_unsupported_types_case_empty_subject_and_invalid_scope(self):
        invalid = [
            "build: compile", "Feat: implement", "feat add thing",
            "feat:", "fix:   ", "feat(): empty scope",
            "feat!: breaking change", "update README",
        ]
        for message in invalid:
            with self.subTest(message=message):
                self.responses.clear()
                self.pr([message])
                code, output = self.run_check()
                self.assertNotEqual(code, 0)
                self.assertIn("::error::", output)

    def test_checks_every_pr_commit_across_pages(self):
        self.pr(["feat: valid"] * 100 + ["invalid"])
        code, output = self.run_check()
        self.assertNotEqual(code, 0)
        self.assertIn("page=2", " ".join(self.visited))
        self.assertIn("invalid", output)

    def test_fails_closed_when_pr_exceeds_api_commit_limit(self):
        self.responses["pulls/19"] = {"commits": 251}
        code, output = self.run_check()
        self.assertNotEqual(code, 0)
        self.assertIn("250", output)

    def test_checks_push_range_but_ignores_merge_commits(self):
        self.responses[f"compare/{self.before}...{self.after}?per_page=100&page=1"] = {
            "total_commits": 2,
            "commits": [
                self.commit("Merge pull request #19 from owner/feature", 1, parents=2),
                self.commit("oops: invalid", 2),
            ],
        }
        code, output = self.run_check(EVENT_NAME="push")
        self.assertNotEqual(code, 0)
        self.assertIn("oops: invalid", output)
        self.responses[f"compare/{self.before}...{self.after}?per_page=100&page=1"]["commits"][1] = self.commit("fix: valid", 2)
        self.assertEqual(self.run_check(EVENT_NAME="push")[0], 0)

    def test_fails_closed_when_push_range_is_truncated(self):
        self.responses[f"compare/{self.before}...{self.after}?per_page=100&page=1"] = {
            "total_commits": 251, "commits": [],
        }
        code, output = self.run_check(EVENT_NAME="push")
        self.assertNotEqual(code, 0)
        self.assertIn("250", output)

    def test_initial_push_checks_event_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps({
                "size": 2,
                "commits": [
                    {"id": "a" * 40, "message": "feat: initial"},
                    {"id": "b" * 40, "message": "bad message"},
                ],
            }))
            code, output = self.run_check(EVENT_NAME="push", PUSH_BEFORE="0" * 40, GITHUB_EVENT_PATH=str(event))
            self.assertNotEqual(code, 0)
            self.assertIn("bad message", output)

    def test_other_events_do_not_validate_synthetic_or_unrelated_commits(self):
        for event in ("merge_group", "schedule", "workflow_dispatch", "release"):
            with self.subTest(event=event):
                self.assertEqual(self.run_check(EVENT_NAME=event)[0], 0)
        self.assertEqual(self.visited, [])


if __name__ == "__main__":
    unittest.main()
