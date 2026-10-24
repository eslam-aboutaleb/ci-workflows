"""Compatibility discovery entry point for the former test location."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.test_workflows import *  # noqa: F403
from tests.test_shared_operations import *  # noqa: F403
