"""Propagate reviewed ci-workflows commit SHA updates to downstream repositories."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DOWNSTREAM_SECRET = 'CI_DOWNSTREAM_REPOSITORIES'
SHA_PATTERN = re.compile(r'^[0-9a-f]{40}$')
REPO_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
WORKFLOW_REF_PATTERN = re.compile(
    r'(eslam-aboutaleb/ci-workflows/\.github/workflows/([A-Za-z0-9_.-]+\.ya?ml)@)([0-9a-f]{40})'
)


def validate_sha(sha: str) -> str:
    value = sha.strip().lower()
    if not SHA_PATTERN.fullmatch(value):
        raise ValueError(f'Target SHA must be a 40-character lowercase hex string, got: {sha!r}')
    return value


def load_downstream() -> list[dict]:
    """Read the versioned registry exclusively from the repository Actions secret."""
    raw = os.environ.get(DOWNSTREAM_SECRET, '')
    if not raw.strip():
        raise ValueError(f'Secret {DOWNSTREAM_SECRET} is required')
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f'Secret {DOWNSTREAM_SECRET} must contain valid JSON') from None
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Downstream registry must be an object with version: 1')
    entries = data.get('repositories')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Downstream registry repositories must be a non-empty list')
    normalized = []
    seen = set()
    for item in entries:
        if isinstance(item, str):
            item = {'repo': item}
        if not isinstance(item, dict) or 'repo' not in item:
            raise ValueError('Invalid downstream repository entry')
        repo = item['repo']
        if not isinstance(repo, str) or not REPO_PATTERN.fullmatch(repo):
            raise ValueError('Invalid downstream repository slug')
        if repo.lower() in seen:
            raise ValueError('Duplicate downstream repository')
        seen.add(repo.lower())
        normalized.append({'repo': repo})
    return normalized


def update_workflow_content(content: str, target_sha: str) -> tuple[str, list[tuple[str, str]]]:
    """Replace ci-workflows commit pins with target_sha.

    Returns (updated_content, list of (workflow_filename, old_sha) that changed).
    """
    target_sha = validate_sha(target_sha)
    changes: list[tuple[str, str]] = []

    def _replace(match: re.Match) -> str:
        prefix, workflow_name, old_sha = match.group(1), match.group(2), match.group(3)
        if old_sha != target_sha:
            changes.append((workflow_name, old_sha))
        return f'{prefix}{target_sha}'

    updated = WORKFLOW_REF_PATTERN.sub(_replace, content)
    return updated, changes


def update_repo_tree(repo_dir: Path, target_sha: str) -> dict[str, list[tuple[str, str]]]:
    """Update all .github/workflows/*.y*ml files in repo_dir to target_sha."""
    target_sha = validate_sha(target_sha)
    workflows_dir = repo_dir / '.github/workflows'
    file_changes: dict[str, list[tuple[str, str]]] = {}
    if not workflows_dir.is_dir():
        return file_changes

    for path in sorted(workflows_dir.iterdir()):
        if not path.is_file() or path.is_symlink() or path.suffix not in {'.yml', '.yaml'}:
            continue
        original = path.read_text()
        updated, changes = update_workflow_content(original, target_sha)
        if updated != original and changes:
            path.write_text(updated)
            rel_path = path.relative_to(repo_dir).as_posix()
            file_changes[rel_path] = changes
    return file_changes


def format_pr_body(target_sha: str, file_changes: dict[str, list[tuple[str, str]]]) -> str:
    short_sha = target_sha[:12]
    old_shas = sorted({old_sha for changes in file_changes.values() for _, old_sha in changes})
    lines = [
        '## Description',
        '',
        f'Automated update pinning reusable workflows from [`eslam-aboutaleb/ci-workflows`](https://github.com/eslam-aboutaleb/ci-workflows) to [`{short_sha}`](https://github.com/eslam-aboutaleb/ci-workflows/commit/{target_sha}).',
        '',
        '### Updated Workflow Pins',
        '',
        '| File | Reusable Workflow | Previous SHA | New SHA |',
        '| :--- | :--- | :--- | :--- |',
    ]
    for rel_path, changes in sorted(file_changes.items()):
        for wf_name, old_sha in changes:
            lines.append(
                f'| `{rel_path}` | `{wf_name}` | `{old_sha[:12]}` | `{short_sha}` |'
            )
    if old_shas:
        lines.extend(['', '### Upstream Diff Links', ''])
        for old_sha in old_shas:
            lines.append(
                f'- [`{old_sha[:12]}...{short_sha}`](https://github.com/eslam-aboutaleb/ci-workflows/compare/{old_sha}...{target_sha})'
            )
    lines.extend([
        '',
        '## Type of Change',
        '',
        '- [ ] Bug fix (non-breaking change which fixes an issue)',
        '- [ ] New feature (non-breaking change which adds functionality)',
        '- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)',
        '- [ ] Documentation update',
        '- [ ] Performance improvement',
        '- [x] CI/CD or tooling enhancement',
        '',
        '## Behavior and Compatibility',
        '',
        'Before: reusable CI jobs reference the previous SHAs listed above. '
        'After: those jobs execute the target upstream revision. '
        'Application source is unchanged; workflow inputs, permissions, secrets, '
        'runner behavior and required checks must be reviewed in the upstream diffs before merging.',
        '',
        '## Verification Evidence',
        '',
        'The propagation script found and replaced the full commit pins listed above. '
        'Downstream CI, including tests/ and workflow validation, is pending. '
        'Application tests, ruff and runtime compatibility checks were not run by the publishing bot; '
        'their results must be confirmed on this PR.',
        '',
        '## Independent Review',
        '',
        'Pending maintainer review of the upstream changes, caller compatibility and downstream CI. '
        'The publishing bot does not provide independent approval.',
        '',
        '## Checklist',
        '',
        "- [ ] My code adheres to the project's coding style guidelines (`ruff check .` and `ruff format --check .`)",
        '- [ ] Changed runtime contracts and compatibility requirements are documented above (or N/A explained)',
        '- [ ] Behavioral tests exercise the changed component boundaries and relevant failure scenarios (or N/A explained)',
        '- [x] Verification commands, results, and skipped checks are recorded above',
        '- [ ] I have updated documentation where appropriate',
    ])
    return '\n'.join(lines) + '\n'


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)


def propagate_to_repo(repo: str, target_sha: str, *, dry_run: bool = False) -> dict:
    target_sha = validate_sha(target_sha)
    if not REPO_PATTERN.fullmatch(repo):
        raise ValueError(f'Invalid repository slug: {repo!r}')

    short_sha = target_sha[:12]
    branch_name = f'chore/ci-workflows-{short_sha}'

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / 'repo'
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if token:
            clone_url = f'https://x-access-token:{token}@github.com/{repo}.git'
            clone_res = _run(['git', 'clone', '--depth', '1', clone_url, str(clone_dir)], check=False)
        else:
            clone_res = _run(['gh', 'repo', 'clone', repo, str(clone_dir), '--', '--depth', '1'], check=False)

        if clone_res.returncode != 0:
            raise RuntimeError(f'Failed to clone {repo}: {clone_res.stderr.strip()}')

        default_branch = _run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=clone_dir
        ).stdout.strip()

        file_changes = update_repo_tree(clone_dir, target_sha)
        if not file_changes:
            return {'repo': repo, 'status': 'up-to-date', 'pr_url': None}

        if dry_run:
            return {
                'repo': repo,
                'status': 'dry-run-would-update',
                'branch': branch_name,
                'files': list(file_changes.keys()),
                'pr_url': None,
            }

        _run(['git', 'checkout', '-b', branch_name], cwd=clone_dir)
        _run(['git', 'config', 'user.name', 'github-actions[bot]'], cwd=clone_dir)
        _run(
            ['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'],
            cwd=clone_dir,
        )
        _run(['git', 'add', '.github/workflows'], cwd=clone_dir)
        commit_title = f'chore(ci): update shared ci-workflows to {short_sha}'
        _run(['git', 'commit', '-m', commit_title], cwd=clone_dir)
        _run(['git', 'push', '--force', 'origin', f'HEAD:refs/heads/{branch_name}'], cwd=clone_dir)

        pr_body = format_pr_body(target_sha, file_changes)
        body_path = Path(tmp) / 'pr-body.md'
        body_path.write_text(pr_body, encoding='utf-8')
        existing_raw = _run(
            ['gh', 'pr', 'list', '--repo', repo, '--head', branch_name, '--state', 'open', '--json', 'number,url'],
            cwd=clone_dir,
        ).stdout
        existing = json.loads(existing_raw)
        if existing:
            pr_url = existing[0]['url']
            pr_number = str(existing[0]['number'])
            _run(
                ['gh', 'pr', 'edit', pr_number, '--repo', repo, '--title', commit_title, '--body-file', str(body_path)],
                cwd=clone_dir,
            )
            status = 'updated-existing-pr'
        else:
            pr_out = _run(
                [
                    'gh', 'pr', 'create',
                    '--repo', repo,
                    '--base', default_branch,
                    '--head', branch_name,
                    '--title', commit_title,
                    '--body-file', str(body_path),
                ],
                cwd=clone_dir,
            ).stdout.strip()
            pr_url = pr_out.splitlines()[-1].strip()
            status = 'created-pr'

        # Close older open bot PRs for previous ci-workflows SHAs
        open_prs_raw = _run(
            ['gh', 'pr', 'list', '--repo', repo, '--state', 'open', '--json', 'number,headRefName,url'],
            cwd=clone_dir,
            check=False,
        )
        if open_prs_raw.returncode == 0:
            for pr in json.loads(open_prs_raw.stdout):
                head_ref = pr.get('headRefName', '')
                if head_ref.startswith('chore/ci-workflows-') and head_ref != branch_name:
                    _run(
                        [
                            'gh', 'pr', 'close', str(pr['number']),
                            '--repo', repo,
                            '--delete-branch',
                            '--comment', f'Superseded by {pr_url}',
                        ],
                        cwd=clone_dir,
                        check=False,
                    )

        return {
            'repo': repo,
            'status': status,
            'branch': branch_name,
            'files': list(file_changes.keys()),
            'pr_url': pr_url,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-sha', default=os.environ.get('TARGET_SHA', ''), help='40-char commit SHA to propagate')
    parser.add_argument('--repo', default=os.environ.get('TARGET_REPO', ''), help='Optional single owner/repo override')
    parser.add_argument('--dry-run', action='store_true', help='Check and report updates without pushing or creating PRs')
    args = parser.parse_args()

    target_sha = args.target_sha
    if not target_sha:
        target_sha = _run(['git', 'rev-parse', 'HEAD'], cwd=ROOT).stdout.strip()
    target_sha = validate_sha(target_sha)

    if args.repo:
        repos = [{'repo': args.repo.strip()}]
    else:
        repos = load_downstream()

    results = []
    for entry in repos:
        result = propagate_to_repo(entry['repo'], target_sha, dry_run=args.dry_run)
        print(json.dumps(result))
        results.append(result)

    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        lines = [
            '## Downstream CI Propagation Summary',
            '',
            f'Target commit: `{target_sha}`',
            '',
            '| Repository | Status | Pull Request |',
            '| :--- | :--- | :--- |',
        ]
        for item in results:
            pr_cell = item['pr_url'] or '—'
            lines.append(f"| `{item['repo']}` | `{item['status']}` | {pr_cell} |")
        with open(summary_path, 'a') as handle:
            handle.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
