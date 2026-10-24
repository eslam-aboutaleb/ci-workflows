"""Resolve every selected Compose image to an immutable digest and platform."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local(value, root, directory=False):
    require(isinstance(value, str) and value and '\n' not in value and '\r' not in value, 'Invalid image configuration path')
    path = Path(value)
    require(not path.is_absolute() and '..' not in path.parts, 'Paths must be repository-relative')
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root), 'Path escapes the checkout')
    require(resolved.is_dir() if directory else resolved.is_file(), 'Image configuration path does not exist')
    return resolved


def command(args):
    result = subprocess.run(args, capture_output=True, text=True)
    require(result.returncode == 0, f'{args[0]} failed while resolving image inventory')
    return json.loads(result.stdout)


def resolve(image, platforms, inspect=command):
    require(isinstance(image, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/:@-]*', image), 'Invalid image reference')
    # Keep existing digest pins: inspecting a mutable tag would validate different content.
    descriptor = inspect(['docker', 'buildx', 'imagetools', 'inspect', image, '--format', '{{json .Manifest}}'])
    digest = descriptor.get('digest', '')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', digest), 'Registry did not return a SHA256 image digest')
    if '@' in image:
        require(image.rsplit('@', 1)[1] == digest, 'Registry digest differs from the pinned image')
    immutable = image.split('@', 1)[0] + '@' + digest
    advertised = {p['platform'].get('os', '') + '/' + p['platform'].get('architecture', '') + ('/' + p['platform']['variant'] if p['platform'].get('variant') else '') for p in descriptor.get('manifests', []) if 'platform' in p}
    if not advertised:
        config = inspect(['docker', 'buildx', 'imagetools', 'inspect', immutable, '--format', '{{json .Image}}'])
        advertised = {config.get('os', '') + '/' + config.get('architecture', '') + ('/' + config['variant'] if config.get('variant') else '')}
    require(set(platforms) <= advertised, f'{image} does not support every required platform: {platforms}')
    return [{'image': immutable, 'platform': platform, 'id': hashlib.sha256(f'{immutable}/{platform}'.encode()).hexdigest()[:20]} for platform in platforms]


def inventory(images_json, files_json, project_dir, platforms_csv, root=None, inspect=command):
    root = (root or Path.cwd()).resolve()
    platforms = platforms_csv.split(',')
    require(platforms and len(platforms) == len(set(platforms)) and all(re.fullmatch(r'linux/(amd64|arm64|arm/v7)', p) for p in platforms), 'Unsupported or duplicate platform')
    images = json.loads(images_json)
    require(isinstance(images, list), 'images must be a JSON array')
    if not images:
        project = local(project_dir, root, directory=True)
        files = json.loads(files_json)
        require(isinstance(files, list) and files and all(isinstance(f, str) for f in files), 'files must be a nonempty JSON array')
        args = ['docker', 'compose']
        for filename in files:
            args.extend(['-f', str(local(filename, project))])
        config = inspect([*args, 'config', '--format', 'json'])
        images = [service['image'] for service in config.get('services', {}).values() if service.get('image')]
    require(images and all(isinstance(image, str) for image in images), 'No image references selected')
    selected = sorted(set(images))
    require(len(selected) * len(platforms) <= 64, 'At most 64 image/platform combinations are supported')
    matrix = [entry for image in selected for entry in resolve(image, platforms, inspect)]
    # Multiple services/tags can resolve to the same immutable image.
    return list({entry['id']: entry for entry in matrix}.values())


if __name__ == '__main__':
    matrix = inventory(os.environ['IMAGES_JSON'], os.environ['FILES_JSON'], os.environ['PROJECT_DIR'], os.environ['PLATFORMS'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write('matrix=' + json.dumps({'include': matrix}, separators=(',', ':')) + '\n')
