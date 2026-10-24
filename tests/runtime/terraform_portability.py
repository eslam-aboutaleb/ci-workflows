"""Real Terraform: move an encrypted plan to a fresh checkout after a provider release."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
OPERATION = ROOT / '.github/actions/terraform-operation/operation.py'


class StateHandler(BaseHTTPRequestHandler):
    """Disposable loopback-only HTTP backend, with no real credentials or cloud access."""
    def do_GET(self):
        self.send_response(200 if self.server.state else 404)
        self.end_headers()
        if self.server.state:
            self.wfile.write(self.server.state)

    def do_POST(self):
        self.server.state = self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def provider_archive(directory, version):
    system = {'Darwin': 'darwin', 'Linux': 'linux'}[platform.system()]
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    filename = f'terraform-provider-random_{version}_{system}_{arch}.zip'
    base = f'https://releases.hashicorp.com/terraform-provider-random/{version}/'
    with urlopen(base + f'terraform-provider-random_{version}_SHA256SUMS', timeout=60) as response:
        checksums = response.read().decode()
    expected = next(line.split()[0] for line in checksums.splitlines() if line.endswith(filename))
    with urlopen(base + filename, timeout=60) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise AssertionError('Provider download checksum mismatch')
    target = directory / filename
    target.write_bytes(payload)
    return target


def main():
    if not shutil.which('terraform'):
        raise SystemExit('Install Terraform 1.12.2 before running the portability regression')
    spec = importlib.util.spec_from_file_location('plan_protection', OPERATION.with_name('protect.py'))
    protection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protection)
    with tempfile.TemporaryDirectory(prefix='terraform-portability-') as directory:
        root = Path(directory).resolve()
        source = root / 'source'
        (source / 'infra').mkdir(parents=True)
        (source / '.gitignore').write_text('.terraform/\n.terraform.lock.hcl\n')
        (source / 'infra/main.tf').write_text('''terraform {
  backend "http" {}
  required_providers {
    random = {
      source = "hashicorp/random"
      version = ">= 3.0.0"
    }
  }
}
resource "random_id" "example" {
  byte_length = 8
}
''')
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        sha = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        mirror = root / 'mirror/registry.terraform.io/hashicorp/random'
        mirror.mkdir(parents=True)
        provider_archive(mirror, '3.6.3')
        config = root / 'terraform.rc'
        config.write_text('provider_installation {\n  filesystem_mirror {\n    path = ' + json.dumps(str(root / 'mirror')) + '\n  }\n}\n')
        server = ThreadingHTTPServer(('127.0.0.1', 0), StateHandler)
        server.state = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_env = {**os.environ, 'TF_CLI_CONFIG_FILE': str(config), 'PROJECT_DIR': 'infra', 'STACK_ID': 'portability', 'TERRAFORM_VERSION': '1.12.2', 'TERRAMATE_VERSION': '', 'GITHUB_SHA': sha, 'GITHUB_REPOSITORY': 'fixture/portability', 'BACKEND_CONFIG': json.dumps({'address': f'http://127.0.0.1:{server.server_port}/state'})}
            checkouts = {}
            runners = {}
            for name in ('plan', 'apply'):
                checkouts[name] = root / f'{name}-checkout'
                runners[name] = root / f'{name}-runner'
                runners[name].mkdir()
                subprocess.run(['git', 'clone', '-q', str(source), str(checkouts[name])], check=True)

            def execute(mode, run_id):
                runner = runners[mode]
                env = {**base_env, 'OPERATION': mode, 'GITHUB_RUN_ID': run_id, 'RUNNER_TEMP': str(runner), 'PLAN_DIR': str(runner / 'artifact'), 'GITHUB_OUTPUT': str(runner / 'outputs'), 'GITHUB_STEP_SUMMARY': str(runner / 'summary'), 'PLAN_SHA': sha, 'PLAN_RUN_ID': '11'}
                result = subprocess.run([sys.executable, str(OPERATION)], cwd=checkouts[mode], env=env, capture_output=True, text=True)
                if result.returncode:
                    raise AssertionError(result.stdout + result.stderr)

            execute('plan', '11')
            plan_artifact = runners['plan'] / 'artifact'
            if '3.6.3' not in (plan_artifact / '.terraform.lock.hcl').read_text():
                raise AssertionError('Plan did not snapshot the selected provider')
            key = secrets.token_hex(32)
            protection.protect('encrypt', plan_artifact, key)
            apply_artifact = runners['apply'] / 'artifact'
            apply_artifact.mkdir()
            shutil.copyfile(plan_artifact / 'plan.enc', apply_artifact / 'plan.enc')
            # No plan-time checkout, .terraform cache, plaintext plan or key paths survive.
            shutil.rmtree(checkouts['plan'])
            shutil.rmtree(runners['plan'])
            provider_archive(mirror, '3.7.2')
            protection.protect('decrypt', apply_artifact, key)
            execute('apply', '12')
            lock = (checkouts['apply'] / 'infra/.terraform.lock.hcl').read_text()
            if '3.6.3' not in lock or '3.7.2' in lock:
                raise AssertionError('Apply changed the reviewed provider selection')
            state = json.loads(server.state or b'{}')
            if not any(resource['type'] == 'random_id' for resource in state.get('resources', [])):
                raise AssertionError('Apply did not write the disposable remote state')
            print('PASS: encrypted plan applied from a fresh checkout against remote state; provider stayed at 3.6.3 after 3.7.2 became available.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
