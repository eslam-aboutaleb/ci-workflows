"""Compatibility entry point; implementation lives in scripts/maintenance."""
from pathlib import Path
import runpy

_target = Path(__file__).resolve().parents[2] / 'scripts/maintenance/check_workflows.py'
if __name__ == '__main__':
    runpy.run_path(str(_target), run_name='__main__')
else:
    globals().update({k: v for k, v in runpy.run_path(str(_target)).items() if not k.startswith('__')})
