"""Conftest for evolve_command tests.

Adds the parent of evolve_command to sys.path so that the package
is importable as `evolve_command.*`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add the plugins/bmad/tools directory to sys.path so `import evolve_command` works
_tools_dir = str(Path(__file__).resolve().parent.parent.parent)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)
