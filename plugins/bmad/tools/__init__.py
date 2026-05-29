"""BMAD plugin tools — Hermes-native tool registrations.

Each module in this package defines tool schemas and handler functions
for a specific BMAD domain (judge, orchestrator, etc.). The schemas and
handlers are consumed by plugins/bmad/__init__.py:register() which calls
ctx.register_tool() for each tool.
"""
