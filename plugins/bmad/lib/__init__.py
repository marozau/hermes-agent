"""BMAD plugin shared library modules.

All lib/ modules are pure-functional (no I/O) except lib/status.py
which handles YAML persistence with atomic writes. lib/ never imports
from hooks/ or commands/.
"""
