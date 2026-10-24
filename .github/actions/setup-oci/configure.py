"""Write isolated OCI credentials for the CLI and Terraform provider."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def configure(config_json, private_key, environ=os.environ):
    config = json.loads(config_json)
    keys = {'user', 'tenancy', 'fingerprint', 'region'}
    if not isinstance(config, dict) or set(config) != keys:
        raise ValueError('oci-config must contain exactly user, tenancy, fingerprint, region')
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9:._-]+', value) for value in config.values()):
        raise ValueError('OCI configuration values must be nonempty single-line identifiers')
    if 'PRIVATE KEY-----' not in private_key:
        raise ValueError('OCI_API_KEY must contain a PEM private key')
    directory = Path(tempfile.mkdtemp(prefix='ci-oci-', dir=environ['RUNNER_TEMP']))
    directory.chmod(0o700)
    key = directory / 'key.pem'
    key.write_text(private_key.rstrip() + '\n')
    key.chmod(0o600)
    cfg = directory / 'config'
    cfg.write_text('[DEFAULT]\n' + ''.join(f'{name}={value}\n' for name, value in config.items()) + f'key_file={key}\n')
    cfg.chmod(0o600)
    values = {
        'OCI_CLI_CONFIG_FILE': str(cfg),
        'OCI_CLI_KEY_FILE': str(key),
        'OCI_PRIVATE_KEY_PATH': str(key),
        'OCI_USER_OCID': config['user'],
        'OCI_TENANCY_OCID': config['tenancy'],
        'OCI_FINGERPRINT': config['fingerprint'],
        'OCI_REGION': config['region'],
        'CI_OCI_CREDENTIAL_DIR': str(directory),
    }
    with open(environ['GITHUB_ENV'], 'a') as stream:
        for name, value in values.items():
            stream.write(f'{name}={value}\n')
    return values


def cleanup(environ=os.environ):
    directory = environ.get('CI_OCI_CREDENTIAL_DIR')
    if directory:
        path = Path(directory).resolve()
        if path.parent != Path(environ['RUNNER_TEMP']).resolve() or not path.name.startswith('ci-oci-'):
            raise ValueError('Invalid OCI credential cleanup directory')
        shutil.rmtree(path, ignore_errors=True)


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['cleanup']:
        cleanup()
    else:
        configure(os.environ['OCI_CONFIG_JSON'], os.environ['OCI_API_KEY'])
