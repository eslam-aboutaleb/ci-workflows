"""Write isolated OCI credentials for the CLI and Terraform provider."""
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile

from cryptography.hazmat.primitives import serialization


def configure(config_json, private_key, environ=os.environ):
    config = json.loads(config_json)
    keys = {'user', 'tenancy', 'fingerprint', 'region'}
    if not isinstance(config, dict) or set(config) != keys:
        raise ValueError('oci-config must contain exactly user, tenancy, fingerprint, region')
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9:._-]+', value) for value in config.values()):
        raise ValueError('OCI configuration values must be nonempty single-line identifiers')
    if 'PRIVATE KEY-----' not in private_key:
        raise ValueError('OCI_API_KEY must contain a PEM private key')
    signing_key = serialization.load_pem_private_key(private_key.encode(), password=None)
    passphrase = secrets.token_urlsafe(32)
    encrypted_key = signing_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase.encode()),
    )
    directory = Path(tempfile.mkdtemp(prefix='ci-oci-', dir=environ['RUNNER_TEMP']))
    directory.chmod(0o700)
    key = directory / 'key.pem'
    with os.fdopen(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(encrypted_key)
    cfg = directory / 'config'
    cfg.write_text('[DEFAULT]\n' + ''.join(f'{name}={value}\n' for name, value in config.items()) + f'key_file={key}\n')
    cfg.chmod(0o600)
    values = {
        'OCI_CLI_CONFIG_FILE': str(cfg),
        'OCI_CLI_KEY_FILE': str(key),
        'OCI_PRIVATE_KEY_PATH': str(key),
        'OCI_PRIVATE_KEY_PASSWORD': passphrase,
        'OCI_CLI_PASSPHRASE': passphrase,
        'OCI_USER_OCID': config['user'],
        'OCI_TENANCY_OCID': config['tenancy'],
        'OCI_FINGERPRINT': config['fingerprint'],
        'OCI_REGION': config['region'],
        'CI_OCI_CREDENTIAL_DIR': str(directory),
    }
    # Terraform 1.12's OCI backend reads OCI_<lowercase attribute>, whereas
    # the OCI provider reads the conventional uppercase environment names.
    values.update({
        'OCI_user_ocid': config['user'],
        'OCI_tenancy_ocid': config['tenancy'],
        'OCI_fingerprint': config['fingerprint'],
        'OCI_region': config['region'],
        'OCI_private_key_path': str(key),
        'OCI_private_key_password': passphrase,
    })
    if environ.get('GITHUB_ACTIONS') == 'true':
        print(f'::add-mask::{passphrase}')
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
