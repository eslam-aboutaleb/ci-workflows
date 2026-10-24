"""Check the contracts that make this repository safe to call centrally."""
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[2]


class WorkflowLoader(yaml.SafeLoader):
    pass


# YAML 1.1 treats the GitHub Actions `on` key as a boolean.
WorkflowLoader.yaml_implicit_resolvers = {
    key: [rule for rule in rules if rule[0] != 'tag:yaml.org,2002:bool']
    for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load(path):
    return yaml.load(path.read_text(), Loader=WorkflowLoader)


def check():
    workflows = {path.name: load(path) for path in sorted((ROOT / '.github/workflows').glob('*.yml'))}
    for name, workflow in workflows.items():
        assert isinstance(workflow, dict) and workflow.get('jobs'), name
        assert workflow['permissions'] == {'contents': 'read'}, name
        assert 'pull_request_target' not in workflow['on'], name
        if name not in {'ci.yml', 'validate.yml', 'propagate.yml', 'operations-smoke.yml'}:
            assert set(workflow['on']) == {'workflow_call'}, name
        for job_id, job in workflow['jobs'].items():
            context = f'{name}:{job_id}'
            permissions = job.get('permissions', workflow['permissions'])
            assert permissions.get('contents') == 'read', context
            if 'runs-on' in job:
                assert job.get('timeout-minutes', 0) > 0, context
                assert 'permissions' in job, context
            if job.get('uses', '').startswith('./'):
                called = workflows[Path(job['uses']).name]['on']['workflow_call'] or {}
                supplied = set(job.get('with', {}))
                declared = called.get('inputs', {})
                assert supplied <= set(declared), (context, supplied - set(declared))
                required = {key for key, value in declared.items() if value.get('required') in {True, 'true'}}
                assert required <= supplied, (context, required - supplied)
            assert job.get('secrets') != 'inherit', context
            if permissions.get('packages') == 'write':
                assert name == 'publish-docker.yml' and job.get('environment') == 'containers', context
            if permissions.get('id-token') == 'write':
                assert name == 'publish-pypi.yml' or job.get('uses', '').endswith('/publish-pypi.yml'), context
            for step in job.get('steps', []):
                action = step.get('uses')
                if not action:
                    continue
                if action.startswith('./.github/actions/'):
                    assert (ROOT / action / 'action.yml').is_file(), (context, action)
                    continue
                assert re.fullmatch(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}', action), (context, action)
                settings = step.get('with', {})
                if action.startswith('actions/checkout@'):
                    assert settings.get('persist-credentials') in {False, 'false'}, context
                if action.startswith('actions/upload-artifact@'):
                    assert 1 <= settings.get('retention-days', 0) <= 30, context
                    assert 'if-no-files-found' in settings, context
        print(f'Contract OK: {name}')
    for path in sorted((ROOT / '.github/actions').glob('*/action.yml')):
        action = load(path)
        assert action['runs']['using'] == 'composite', path
        for step in action['runs']['steps']:
            if 'run' in step:
                assert step.get('shell') == 'bash', path
                assert '${{ inputs.' not in step['run'], (path, 'Pass inputs through environment variables')
            if 'uses' in step:
                assert re.fullmatch(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}', step['uses']), (path, step['uses'])
        print(f'Contract OK: {path.parent.name} composite action')
    jobs = workflows['ci.yml']['jobs']
    gate = jobs['quality-gate']
    assert gate['name'] == 'Quality gate' and gate['if'] == 'always()'
    assert set(gate['needs']) == set(jobs) - {'quality-gate', 'publish-pypi'}
    assert 'quality-gate' in jobs['publish-pypi']['needs']


if __name__ == '__main__':
    check()
