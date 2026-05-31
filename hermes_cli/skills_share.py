"""Share and unshare skills between profile-local and shared directories.

``hermes skills share <name>``
    Move (default) or copy (``--copy``) a skill from the active profile's
    skills directory to ``~/.hermes/skills/``, the shared skill store.

``hermes skills unshare <name>``
    Remove a skill from the shared directory.  By default the skill is
    moved back to the active profile's skills directory; pass
    ``--no-reinstate`` to just delete it from shared.

Shared skills live in the default profile's skills directory
(``<hermes_root>/skills/``) and are loaded by profiles that have
``skills.shared: true`` in their config.  A ``.shared.yaml`` sidecar
manifest tracks which skills were explicitly shared so the curator can
tell shared from bundled.
"""

import shutil
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from hermes_constants import get_default_hermes_root, get_hermes_home
from hermes_cli.colors import Colors, color


def _shared_skills_dir() -> Path:
    """Return the shared skills directory (always the root skills dir)."""
    return get_default_hermes_root() / "skills"


def _shared_manifest_path() -> Path:
    """Return the path to the .shared.yaml sidecar manifest."""
    return _shared_skills_dir() / ".shared.yaml"


def _profile_skills_dir() -> Path:
    """Return the active profile's skills directory."""
    return get_hermes_home() / "skills"


def _is_default_profile() -> bool:
    """Return True when the active profile IS the default profile."""
    hermes_home = get_hermes_home()
    default_root = get_default_hermes_root()
    try:
        return hermes_home.resolve() == default_root.resolve()
    except OSError:
        return False


def _read_manifest() -> Dict:
    """Read the .shared.yaml manifest, returning {} on any error."""
    mp = _shared_manifest_path()
    if not mp.exists():
        return {}
    try:
        data = yaml.safe_load(mp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_manifest(data: Dict) -> None:
    """Atomically write the .shared.yaml manifest."""
    mp = _shared_manifest_path()
    mp.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    tmp = mp.with_suffix(mp.suffix + ".tmp")
    tmp.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True),
                   encoding="utf-8")
    tmp.replace(mp)


def cmd_skills_share(args) -> int:
    """Handle ``hermes skills share <name> [--copy]``."""
    if _is_default_profile():
        print(color(
            "Error: 'skills share' does nothing in the default profile.\n"
            "  The default profile's skills directory IS the shared directory.\n"
            "  Run from a non-default profile (e.g. hermes -p engineer skills share ...).",
            Colors.RED,
        ))
        return 1

    name: str = args.name
    profile_dir = _profile_skills_dir()
    skill_path = profile_dir / name

    if not skill_path.is_dir():
        print(color(f"Error: Skill '{name}' not found in {profile_dir}", Colors.RED))
        return 1

    dest = _shared_skills_dir() / name
    if dest.exists():
        print(color(
            f"Error: Skill '{name}' already exists in the shared directory.",
            Colors.RED,
        ))
        return 1

    use_copy: bool = getattr(args, "copy", False)

    if use_copy:
        shutil.copytree(skill_path, dest)
        print(color(
            f"Copied '{name}' to shared directory (local copy retained).",
            Colors.GREEN,
        ))
    else:
        shutil.move(str(skill_path), str(dest))
        print(color(
            f"Moved '{name}' from profile to shared directory.",
            Colors.GREEN,
        ))

    # Update shared manifest
    manifest = _read_manifest()
    manifest[name] = {
        "source_profile": get_hermes_home().name,
        "shared_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_manifest(manifest)

    if use_copy:
        print(color(
            "  The local copy is still active for this profile (union loading).\n"
            "  Other profiles with skills.shared: true will also see it.",
            Colors.DIM,
        ))
    else:
        print(color(
            "  This skill is now only in the shared directory.  It will still\n"
            "  be visible to this profile (union loading loads shared after local).\n"
            "  Other profiles with skills.shared: true will also see it.",
            Colors.DIM,
        ))

    return 0


def cmd_skills_unshare(args) -> int:
    """Handle ``hermes skills unshare <name> [--no-reinstate]``."""
    name: str = args.name
    shared_path = _shared_skills_dir() / name

    if not shared_path.is_dir():
        # Check manifest — it might be listed but already removed
        manifest = _read_manifest()
        if name in manifest:
            manifest.pop(name)
            _write_manifest(manifest)
            print(color(
                f"Skill '{name}' was in the shared manifest but the directory "
                f"is already gone.  Cleaned up manifest entry.",
                Colors.YELLOW,
            ))
            return 0
        print(color(
            f"Error: Skill '{name}' not found in shared directory.",
            Colors.RED,
        ))
        return 1

    reinstate: bool = not getattr(args, "no_reinstate", False)
    is_default = _is_default_profile()

    if reinstate and not is_default:
        profile_dir = _profile_skills_dir()
        dest = profile_dir / name
        if dest.exists():
            print(color(
                f"Error: Skill '{name}' already exists in profile skills.\n"
                f"  Remove or rename the local copy first, or use --no-reinstate.",
                Colors.RED,
            ))
            return 1
        shutil.move(str(shared_path), str(dest))
        print(color(
            f"Moved '{name}' from shared back to profile skills.",
            Colors.GREEN,
        ))
    else:
        shutil.rmtree(shared_path)
        if is_default:
            print(color(
                f"Removed '{name}' from skills directory (default profile).",
                Colors.GREEN,
            ))
        else:
            print(color(
                f"Removed '{name}' from shared directory.",
                Colors.GREEN,
            ))

    # Update manifest
    manifest = _read_manifest()
    if name in manifest:
        manifest.pop(name)
        _write_manifest(manifest)

    return 0
