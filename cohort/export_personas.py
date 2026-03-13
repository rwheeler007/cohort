"""Export Cohort agent personas as lightweight .md files for Claude Code.

Reads each agent's agent_config.json and agent_prompt.md, writes a compact
persona file to .claude/agents/{agent_id}.md.  These files are what Claude Code
reads when it looks for sub-agents in the project or global ~/.claude/agents/.

The output files contain only identity, role, and personality -- no memory,
no channel history.  They are safe to junction/symlink globally.

Usage::

    python -m cohort.export_personas           # writes to <cohort_root>/.claude/agents/
    python -m cohort.export_personas --dry-run # print what would be written
    python -m cohort.export_personas --force   # overwrite existing files

Can also be imported:

    from cohort.export_personas import export_all_personas
    export_all_personas(cohort_root)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


# =====================================================================
# Core generator
# =====================================================================

_PERSONA_TEMPLATE = """\
---
name: {name}
role: {role}
---

{body}
"""


def _build_persona_body(config: dict, prompt_text: Optional[str]) -> str:
    """Build the markdown body for a persona file.

    Prioritises agent_prompt.md (richer), falls back to assembling
    a compact block from agent_config.json fields.
    """
    if prompt_text and len(prompt_text.strip()) > 50:
        return prompt_text.strip()

    # Fallback: assemble from config
    parts: list[str] = []

    personality = config.get("personality", "").strip()
    if personality:
        parts.append(personality)

    capabilities: list[str] = config.get("capabilities", [])
    if capabilities:
        cap_lines = "\n".join(f"- {c}" for c in capabilities[:8])
        parts.append(f"## Capabilities\n\n{cap_lines}")

    domain: list[str] = config.get("domain_expertise", [])
    if domain:
        dom_lines = "\n".join(f"- {d}" for d in domain[:8])
        parts.append(f"## Domain Expertise\n\n{dom_lines}")

    return "\n\n".join(parts) if parts else f"{config.get('role', '')} agent."


def export_agent_persona(
    agent_dir: Path,
    output_dir: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[bool, str]:
    """Export a single agent's persona file.

    Returns (success, message).
    """
    agent_id = agent_dir.name
    cfg_path = agent_dir / "agent_config.json"
    prompt_path = agent_dir / "agent_prompt.md"
    out_path = output_dir / f"{agent_id}.md"

    if not cfg_path.exists():
        return False, f"  [!] {agent_id}: no agent_config.json, skipped"

    try:
        config = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"  [X] {agent_id}: could not read config -- {exc}"

    name = config.get("name") or agent_id.replace("_", " ").title()
    role = config.get("role", "AI Agent")

    prompt_text: Optional[str] = None
    if prompt_path.exists():
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
        except OSError:
            pass

    body = _build_persona_body(config, prompt_text)
    content = _PERSONA_TEMPLATE.format(name=name, role=role, body=body)

    if out_path.exists() and not force:
        return True, f"  [*] {agent_id}: already exists (use --force to overwrite)"

    if dry_run:
        preview = content[:120].replace("\n", " ")
        return True, f"  [>>] {agent_id}: would write {len(content)} bytes -- {preview}..."

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        return True, f"  [OK] {agent_id}: wrote {out_path.name}"
    except OSError as exc:
        return False, f"  [X] {agent_id}: write failed -- {exc}"


def export_all_personas(
    cohort_root: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, int]:
    """Export all agent personas from cohort_root/agents/ to cohort_root/.claude/agents/.

    Returns (success_count, fail_count).
    """
    agents_dir = cohort_root / "agents"
    output_dir = cohort_root / ".claude" / "agents"

    if not agents_dir.is_dir():
        print(f"  [X] Agents directory not found: {agents_dir}", file=sys.stderr)
        return 0, 0

    ok = 0
    fail = 0
    for agent_dir in sorted(agents_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        if not (agent_dir / "agent_config.json").exists():
            continue

        success, msg = export_agent_persona(
            agent_dir, output_dir, dry_run=dry_run, force=force,
        )
        print(msg)
        if success:
            ok += 1
        else:
            fail += 1

    return ok, fail


# =====================================================================
# CLI
# =====================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Cohort agent personas as .md files for Claude Code.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written without creating files.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing persona files.",
    )
    parser.add_argument(
        "--cohort-root", type=Path, default=None,
        help="Path to Cohort root (default: auto-detect from this file's location).",
    )
    args = parser.parse_args()

    cohort_root = args.cohort_root or Path(__file__).resolve().parent.parent

    print()
    print("Exporting agent personas...")
    print(f"  Source:  {cohort_root / 'agents'}")
    print(f"  Output:  {cohort_root / '.claude' / 'agents'}")
    if args.dry_run:
        print("  Mode:    dry-run (no files written)")
    print()

    ok, fail = export_all_personas(cohort_root, dry_run=args.dry_run, force=args.force)

    print()
    if fail == 0:
        print(f"  [OK] Done. {ok} persona files {'would be ' if args.dry_run else ''}written.")
    else:
        print(f"  [!] Done. {ok} succeeded, {fail} failed.")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
