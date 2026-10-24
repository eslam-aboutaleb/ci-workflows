"""Exercise legacy Python caller installs and builds in a disposable environment."""
import os, pathlib, subprocess, tempfile
import yaml
root=pathlib.Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='ci-uv-consumer-') as tmp:
 p=pathlib.Path(tmp)
 subprocess.run(['uv','venv','--python','3.12',str(p/'.venv')],check=True)
 env=os.environ|{'PATH':str(p/'.venv/bin')+os.pathsep+os.environ['PATH'],'RUFF_VERSION':'0.14.14','EXTRAS':'test','CONSTRAINTS':'constraints.txt'}
 (p/'pyproject.toml').write_text('''[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"
[project]
name = "ci-uv-smoke"
version = "0.0.1"
description = "Shared CI compatibility smoke package"
readme = "README.md"
requires-python = ">=3.11"
[tool.setuptools]
py-modules = ["smoke"]
[project.optional-dependencies]
test = ["packaging>=24"]
''')
 (p/'README.md').write_text('CI smoke package.\n')
 (p/'smoke.py').write_text('def answer():\n    return 42\n')
 (p/'test_smoke.py').write_text('from smoke import answer\n\n\ndef test_answer():\n    assert answer() == 42\n')
 (p/'constraints.txt').write_text('packaging==24.2\npytest==8.4.2\n')
 workflow=yaml.safe_load((root/'.github/workflows/python.yml').read_text())
 for job,name in [('lint','Run pinned Ruff'),('test','Install pip project with caller constraints'),('build','Build and inspect metadata')]:
  step=next(s for s in workflow['jobs'][job]['steps'] if s.get('name')==name)
  subprocess.run(['bash','-euc',step['run']],cwd=p,env=env,check=True)
 subprocess.run(['python','-m','pytest','-q'],cwd=p,env=env,check=True)
 subprocess.run(['python','-c','import packaging; assert packaging.__version__ == "24.2"'],cwd=p,env=env,check=True)
 print('Editable extras, constraints, Ruff, pytest and isolated uv builds passed')
