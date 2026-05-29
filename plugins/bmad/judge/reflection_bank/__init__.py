"""Reflection Bank — persistent store of post-phase reflection entries.

Schema: 17-field YAML entries for structured mistake/pattern tracking.
Storage: per-project (.hermes/reflection-bank.yaml) and global (~/.hermes/reflection-bank/global.yaml).
"""

from plugins.bmad.judge.reflection_bank.schema import (
    ReflectionEntry,
    Severity,
    create_entry,
    load_entries,
    dump_entries,
)
from plugins.bmad.judge.reflection_bank.persistence import (
    BankStore,
    get_global_store,
    get_project_store,
)
from plugins.bmad.judge.reflection_bank.query import (
    Query,
    search,
)

__all__ = [
    "ReflectionEntry",
    "Severity",
    "create_entry",
    "load_entries",
    "dump_entries",
    "BankStore",
    "get_global_store",
    "get_project_store",
    "Query",
    "search",
]
