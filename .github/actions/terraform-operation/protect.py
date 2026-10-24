"""Authenticated encryption of binary plans and diagnostic output at rest."""
import io
import os
from pathlib import Path
import re
import sys
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FILES = {'tfplan', 'manifest.json', 'plan.txt'}
HEADER = b'ci-workflows-plan-v1\x00'


def protect(mode, directory, key_hex):
    if not re.fullmatch(r'[0-9a-fA-F]{64}', key_hex):
        raise ValueError('PLAN_ENCRYPTION_KEY must be a 64-character random hexadecimal secret')
    cipher = AESGCM(bytes.fromhex(key_hex))
    directory = Path(directory)
    if mode == 'encrypt':
        if not directory.exists():
            return  # No plan/diagnostic files were produced before failure.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(FILES):
                path = directory / name
                if path.is_file() and not path.is_symlink():
                    archive.write(path, name)
        nonce = os.urandom(12)
        (directory / 'plan.enc').write_bytes(HEADER + nonce + cipher.encrypt(nonce, buffer.getvalue(), HEADER))
        for name in FILES:
            (directory / name).unlink(missing_ok=True)
    elif mode == 'decrypt':
        payload = (directory / 'plan.enc').read_bytes()
        if not payload.startswith(HEADER):
            raise ValueError('Unsupported encrypted plan format')
        nonce = payload[len(HEADER):len(HEADER) + 12]
        decoded = cipher.decrypt(nonce, payload[len(HEADER) + 12:], HEADER)
        with zipfile.ZipFile(io.BytesIO(decoded)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or not set(names) <= FILES or not {'tfplan', 'manifest.json'} <= set(names):
                raise ValueError('Encrypted plan has unexpected or missing files')
            for name in names:
                path = directory / name
                if path.is_symlink():
                    raise ValueError('Plan destination must not be a symlink')
                path.write_bytes(archive.read(name))
                path.chmod(0o600)
    else:
        raise ValueError('Unknown plan protection operation')


if __name__ == '__main__':
    protect(sys.argv[1], os.environ['PLAN_DIR'], os.environ['PLAN_ENCRYPTION_KEY'])
