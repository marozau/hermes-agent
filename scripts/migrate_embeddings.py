#!/usr/bin/env python3
"""Story 11.4: Backfill missing .vec embedding sidecars.

One-shot migration script. Walks all trajectory entries, batches embedding
calls, writes missing sidecars. Idempotent — skips entries with existing
sidecar files.

Usage:
    python scripts/migrate_embeddings.py [--memory-dir PATH] [--batch-size N] [--dry-run]
"""
import argparse
import os
import sys
from pathlib import Path


def parse_entry_body(filepath: Path) -> tuple[str, str]:
    """Parse YAML frontmatter to extract entry_id and body."""
    content = filepath.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return "", ""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return "", ""
    import yaml
    fm = yaml.safe_load(parts[1])
    if not fm or not isinstance(fm, dict):
        return "", ""
    entry_id = fm.get("id", "")
    body = parts[2].strip()
    return entry_id, body


def main():
    parser = argparse.ArgumentParser(description="Backfill .vec embedding sidecars")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Add project root to path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from lib.hermes_llm import llm_embed, load_providers_config
    from lib.hermes_memory import _resolve_memory_dir

    mem_dir = _resolve_memory_dir(args.memory_dir)
    providers = load_providers_config()
    wl = providers.get("recall_embed")
    if not wl:
        print("ERROR: recall_embed workload not found in providers.yaml")
        sys.exit(1)

    provider = wl.primary.provider
    model = wl.primary.model.lower().replace("/", "-")
    suffix = f".{provider}-{model}.vec"

    # Find all .md entry files missing sidecars
    entries = []
    for f in sorted(mem_dir.glob("*.md")):
        entry_id, body = parse_entry_body(f)
        if not entry_id or not body:
            continue
        sidecar = mem_dir / f"{entry_id}{suffix}"
        if sidecar.exists():
            continue
        entries.append((entry_id, body, f))

    print(f"Found {len(entries)} entries missing sidecars")
    if args.dry_run:
        for eid, _, _ in entries[:10]:
            print(f"  would backfill: {eid}")
        if len(entries) > 10:
            print(f"  ... and {len(entries) - 10} more")
        return

    import numpy

    total = len(entries)
    for i in range(0, total, args.batch_size):
        batch = entries[i : i + args.batch_size]
        bodies = [b for _, b, _ in batch]
        result = llm_embed(bodies)
        if not result:
            print(f"Batch {i // args.batch_size}: llm_embed failed, skipping")
            continue
        for (entry_id, _, filepath), vec in zip(batch, result):
            if vec is None:
                continue
            sidecar = mem_dir / f"{entry_id}{suffix}"
            tmp = sidecar.with_suffix(".vec.tmp")
            numpy.array(vec, dtype=numpy.float32).tofile(str(tmp))
            os.replace(tmp, sidecar)
        print(
            f"Batch {i // args.batch_size}: wrote "
            f"{sum(1 for v in result if v is not None)} sidecars"
        )

    print(f"Done. {total} entries processed.")


if __name__ == "__main__":
    main()
