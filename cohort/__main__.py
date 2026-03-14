"""Cohort CLI entry point.

Usage::

    python -m cohort say --sender architect --channel review --file conv.jsonl --message "Hello"
    python -m cohort gate --agent architect --channel review --file conv.jsonl --agents agents.json
    python -m cohort next-speaker --channel review --file conv.jsonl --agents agents.json
    python -m cohort briefing generate --hours 24
    python -m cohort setup
    python -m cohort tutorial
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# =====================================================================
# CLI command handlers
# =====================================================================

def _cmd_gate(args: argparse.Namespace) -> int:
    """Should this agent respond? Exit 0 = speak, exit 1 = don't."""
    from cohort.chat import Channel, ChatManager
    from cohort.file_transport import JsonlFileStorage, load_agents_from_file
    from cohort.meeting import (
        STAKEHOLDER_THRESHOLDS,
        StakeholderStatus,
        calculate_contribution_score,
        extract_keywords,
        initialize_meeting_context,
        should_agent_speak,
    )

    storage = JsonlFileStorage(args.file)
    chat = ChatManager(storage)
    agents = load_agents_from_file(args.agents)

    if args.agent not in agents:
        print(f"[X] Agent '{args.agent}' not found in {args.agents}", file=sys.stderr)
        return 2

    messages = chat.get_channel_messages(args.channel, limit=15)
    if not messages:
        print(f"[X] No messages in channel '{args.channel}'", file=sys.stderr)
        return 1

    # Build meeting context from agents
    meeting_ctx = initialize_meeting_context(list(agents.keys()))
    recent_text = " ".join(m.content for m in messages[-5:])
    topic_kw = extract_keywords(recent_text)
    meeting_ctx["current_topic"]["keywords"] = topic_kw

    # Build or update channel
    channel = chat.get_channel(args.channel)
    if channel is None:
        channel = Channel(
            id=args.channel, name=args.channel, description="",
            created_at="", meeting_context=meeting_ctx,
        )
    else:
        channel.meeting_context = meeting_ctx

    agent_config = agents[args.agent]
    last_message = messages[-1]
    status = StakeholderStatus.ACTIVE.value
    threshold = STAKEHOLDER_THRESHOLDS[status]

    speak = should_agent_speak(
        args.agent, last_message, channel, chat, agent_config,
    )
    score = calculate_contribution_score(
        args.agent, "[considering response]",
        meeting_ctx, agent_config, messages,
    )

    decision = "SPEAK" if speak else "SILENT"
    reason = (
        f"score {score:.2f} >= threshold {threshold:.2f}"
        if speak
        else f"score {score:.2f} < threshold {threshold:.2f}"
    )

    if args.format == "json":
        print(json.dumps({
            "agent": args.agent,
            "score": round(score, 4),
            "threshold": threshold,
            "status": status,
            "speak": speak,
            "reason": reason,
        }))
    else:
        print(f"Agent:     {args.agent}")
        print(f"Score:     {score:.2f}")
        print(f"Threshold: {threshold:.2f} ({status})")
        print(f"Decision:  {decision}")
        print(f"Reason:    {reason}")

    return 0 if speak else 1


def _cmd_next_speaker(args: argparse.Namespace) -> int:
    """Who should talk next? Ranked by composite relevance."""
    from cohort.chat import ChatManager
    from cohort.file_transport import JsonlFileStorage, load_agents_from_file
    from cohort.meeting import (
        calculate_composite_relevance,
        extract_keywords,
        initialize_meeting_context,
    )

    storage = JsonlFileStorage(args.file)
    chat = ChatManager(storage)
    agents = load_agents_from_file(args.agents)

    messages = chat.get_channel_messages(args.channel, limit=15)
    if not messages:
        print(f"[X] No messages in channel '{args.channel}'", file=sys.stderr)
        return 1

    # Build meeting context
    meeting_ctx = initialize_meeting_context(list(agents.keys()))
    recent_text = " ".join(m.content for m in messages[-5:])
    topic_kw = extract_keywords(recent_text)
    meeting_ctx["current_topic"]["keywords"] = topic_kw

    # Score all agents
    scores: list[dict[str, Any]] = []
    for agent_id, agent_config in agents.items():
        relevance = calculate_composite_relevance(
            agent_id=agent_id,
            meeting_context=meeting_ctx,
            agent_config=agent_config,
            recent_messages=messages,
        )
        scores.append({
            "agent_id": agent_id,
            "score": relevance["composite_total"],
            "phase": relevance.get("detected_phase", "unknown"),
            "breakdown": {
                k: round(v, 3) for k, v in relevance.items()
                if k not in ("composite_total", "detected_phase")
            },
        })
    scores.sort(key=lambda x: x["score"], reverse=True)
    top = scores[: args.top]

    if args.format == "json":
        print(json.dumps(top, indent=2))
    else:
        print(f"Next speaker for channel '{args.channel}':")
        for i, entry in enumerate(top, 1):
            bd = entry["breakdown"]
            top_dims = sorted(bd.items(), key=lambda x: x[1], reverse=True)[:2]
            dims_str = ", ".join(f"{k}={v:.2f}" for k, v in top_dims)
            print(
                f"  {i}. {entry['agent_id']:20s}  "
                f"score={entry['score']:.2f}  "
                f"phase={entry['phase']}  ({dims_str})"
            )

    return 0


def _cmd_briefing(args: argparse.Namespace) -> int:
    """Generate or view executive briefings."""
    import os
    from pathlib import Path

    from cohort.chat import ChatManager
    from cohort.executive_briefing import ExecutiveBriefing
    from cohort.registry import create_storage

    resolved_dir = os.environ.get("COHORT_DATA_DIR", getattr(args, "data_dir", "data"))
    storage = create_storage(resolved_dir)
    chat = ChatManager(storage)

    # Try to load work queue (optional)
    work_queue = None
    try:
        from cohort.work_queue import WorkQueue
        work_queue = WorkQueue(Path(resolved_dir))
    except Exception:
        pass

    briefing = ExecutiveBriefing(
        data_dir=Path(resolved_dir),
        chat=chat,
        work_queue=work_queue,
    )

    brief_cmd = getattr(args, "brief_command", None)

    if brief_cmd == "generate":
        report = briefing.generate(
            hours=args.hours,
            post_to_channel=not args.no_post,
        )
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.to_text())
        return 0

    elif brief_cmd == "latest":
        report = briefing.get_latest()
        if report is None:
            print("[X] No briefings found.", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.to_text())
        return 0

    else:
        print("Usage: python -m cohort briefing [generate|latest]", file=sys.stderr)
        return 1


def _cmd_say(args: argparse.Namespace) -> int:
    """Append a message to the conversation."""
    from cohort.chat import ChatManager
    from cohort.file_transport import JsonlFileStorage

    storage = JsonlFileStorage(args.file)
    chat = ChatManager(storage)

    # Auto-create channel if needed
    if chat.get_channel(args.channel) is None:
        chat.create_channel(name=args.channel, description=args.channel)

    msg = chat.post_message(
        channel_id=args.channel,
        sender=args.sender,
        content=args.message,
    )
    print(f"[OK] {msg.id} -> #{args.channel}")
    return 0


# =====================================================================
# cohort new / cohort link / cohort export-personas
# -- shared helpers --
# =====================================================================

_GITIGNORE_TEMPLATE = """\
# Cohort working memory -- project-specific, never commit
.cohort-memory/

# Secrets -- never commit
.env
*.pem
*.key
*.p12
*_secret*
*_credentials*

# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/

# Node
node_modules/
.next/
dist/

# OS / IDE
.DS_Store
Thumbs.db
Desktop.ini
.vscode/
.idea/
*.swp
"""

_ENV_EXAMPLE_TEMPLATE = """\
# Copy this file to .env and fill in your values.
# .env is git-ignored -- never commit secrets.

# Example: API keys
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...

# Example: Database
# DATABASE_URL=postgresql://user:password@localhost:5432/mydb

# Example: App config
# DEBUG=false
# PORT=8000
"""

_PROFILE_TOOLS = {
    "developer":  ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    "readonly":   ["Read", "Glob", "Grep"],
    "researcher": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
}


def _review_permissions(manifest: "CohortManifest", project_dir: Path) -> "CohortManifest":
    """Interactively show and edit the manifest permissions.

    Prints the current permission plan, lets the user adjust profile,
    allow paths, and deny paths.  Returns the (possibly updated) manifest.
    """
    from cohort.project_manifest import ProjectPermissions

    p = manifest.permissions
    print()
    print("  Agent Permissions for this project:")
    print()
    print(f"    Profile:      {p.profile}")
    print(f"    Tools:        {', '.join(p.allowed_tools)}")
    print(f"    Allow paths:  {p.allow_paths or ['(project root only)']}")
    print(f"    Deny paths:   {p.deny_paths or ['(none)']}")
    print(f"    Max turns:    {p.max_turns}")
    print()

    raw = input("  Adjust? [Y/n] ").strip().lower()
    if raw not in ("", "y", "yes"):
        return manifest

    # --- Profile ---
    print()
    print("  Profiles:")
    print("    1. developer  -- Read, Write, Edit, Bash, Glob, Grep")
    print("    2. readonly   -- Read, Glob, Grep only")
    print("    3. researcher -- Read + WebSearch, WebFetch")
    print("    4. custom     -- enter tools manually")
    print()
    choice = input(f"  Profile [{p.profile}]: ").strip()
    profile_map = {"1": "developer", "2": "readonly", "3": "researcher", "4": "custom"}
    new_profile = profile_map.get(choice, p.profile) if choice else p.profile

    if new_profile == "custom":
        raw_tools = input(f"  Tools (comma-separated) [{', '.join(p.allowed_tools)}]: ").strip()
        new_tools = [t.strip() for t in raw_tools.split(",") if t.strip()] if raw_tools else p.allowed_tools
    else:
        new_tools = _PROFILE_TOOLS.get(new_profile, p.allowed_tools)

    # --- Allow paths (additional) ---
    print()
    print(f"  Allow paths (agents may read/write here).")
    print(f"  Current: {p.allow_paths or [str(project_dir)]}")
    raw_allow = input("  Add more? (comma-separated paths, or Enter to keep): ").strip()
    new_allow = list(p.allow_paths) or [str(project_dir)]
    if raw_allow:
        for ap in raw_allow.split(","):
            ap = ap.strip()
            if ap and ap not in new_allow:
                new_allow.append(ap)

    # --- Deny paths ---
    print()
    print(f"  Deny paths (agents must NEVER touch these).")
    print(f"  Current: {p.deny_paths or ['(none)']}")
    raw_deny = input("  Add more? (comma-separated paths, or Enter to keep): ").strip()
    new_deny = list(p.deny_paths)
    if raw_deny:
        for dp in raw_deny.split(","):
            dp = dp.strip()
            if dp and dp not in new_deny:
                new_deny.append(dp)

    # --- Max turns ---
    raw_turns = input(f"  Max turns per task [{p.max_turns}]: ").strip()
    try:
        new_turns = int(raw_turns) if raw_turns else p.max_turns
    except ValueError:
        new_turns = p.max_turns

    # Rebuild manifest with updated permissions
    updated_perms = ProjectPermissions(
        profile=new_profile,
        allow_paths=new_allow,
        deny_paths=new_deny,
        allowed_tools=new_tools,
        max_turns=new_turns,
    )
    from dataclasses import replace as _replace
    return _replace(manifest, permissions=updated_perms)


def _write_housekeeping(project_dir: Path, *, skip_gitignore: bool = False) -> None:
    """Write .gitignore, .env.example, and an empty .env if missing."""
    # .gitignore -- merge if exists, create if not
    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")
        additions = []
        for line in _GITIGNORE_TEMPLATE.splitlines():
            if line and not line.startswith("#") and line not in existing:
                additions.append(line)
        if additions:
            gitignore.write_text(existing.rstrip() + "\n" + "\n".join(additions) + "\n", encoding="utf-8")
            print(f"  [OK] Updated .gitignore ({len(additions)} entries added)")
        else:
            print(f"  [*] .gitignore already complete")
    elif not skip_gitignore:
        gitignore.write_text(_GITIGNORE_TEMPLATE, encoding="utf-8")
        print(f"  [OK] Created .gitignore")

    # .env.example -- always write if missing
    env_example = project_dir / ".env.example"
    if not env_example.exists():
        env_example.write_text(_ENV_EXAMPLE_TEMPLATE, encoding="utf-8")
        print(f"  [OK] Created .env.example")

    # .env -- create empty if missing (gitignored)
    env_file = project_dir / ".env"
    if not env_file.exists():
        env_file.write_text("# Project secrets -- do not commit\n", encoding="utf-8")
        print(f"  [OK] Created .env (empty, gitignored)")


def _open_in_vscode(project_dir: Path) -> None:
    """Open project_dir in VS Code if the CLI is available."""
    import shutil as _sh
    import subprocess as _sp

    # VS Code CLI may be 'code' or 'code.cmd' on Windows
    code_cmd = _sh.which("code") or _sh.which("code.cmd")
    if not code_cmd:
        print(f"  [*] VS Code CLI not found -- open manually: {project_dir}")
        return

    try:
        _sp.Popen([code_cmd, str(project_dir)], close_fds=True)
        print(f"  [OK] Opened in VS Code: {project_dir.name}")
    except OSError as exc:
        print(f"  [!] Could not open VS Code: {exc}")


def _cmd_new(args: argparse.Namespace) -> int:
    """Create a new project directory linked to Cohort."""
    import subprocess as _sp
    from cohort.project_manifest import CohortManifest, load_cohort_settings

    parent = (Path(args.dir) if args.dir else Path.cwd()).resolve()
    project_dir = parent / args.name

    if project_dir.exists():
        print(f"[X] Directory already exists: {project_dir}")
        print(f"    Use 'cohort link --dir {project_dir}' to link an existing project.")
        return 1

    cohort_root = Path(__file__).resolve().parent.parent
    settings = load_cohort_settings(cohort_root)

    print()
    print(f"  Creating project: {args.name}")
    print(f"  Location:         {project_dir}")
    print()

    # Create directory
    project_dir.mkdir(parents=True)
    print(f"  [OK] Created {project_dir}")

    # Git init
    git_ok = False
    if not args.no_git:
        try:
            _sp.run(
                ["git", "init", str(project_dir)],
                check=True, capture_output=True, text=True,
            )
            print(f"  [OK] Initialized git repository")
            git_ok = True
        except (_sp.CalledProcessError, FileNotFoundError) as exc:
            print(f"  [!] git init skipped: {exc}")

    # Housekeeping: .gitignore, .env.example, .env
    print()
    _write_housekeeping(project_dir, skip_gitignore=not git_ok)

    # Build manifest from settings defaults
    manifest = CohortManifest.create(
        project_dir=project_dir,
        cohort_root=cohort_root,
        project_name=args.name,
        cohort_settings=settings,
    )

    # Review + edit permissions interactively (unless --no-review)
    if not getattr(args, "no_review", False):
        manifest = _review_permissions(manifest, project_dir)

    # Write .cohort manifest
    manifest_path = manifest.write(project_dir)
    print(f"  [OK] Created {manifest_path.name} manifest")

    # Create working memory directory
    manifest.ensure_working_memory_dir(project_dir)
    print(f"  [OK] Created working memory directory: {manifest.working_memory_path}")

    print()
    print(f"  Project ready.")
    print()

    # Open in VS Code
    if not getattr(args, "no_code", False):
        _open_in_vscode(project_dir)

    print()
    return 0


def _cmd_link(args: argparse.Namespace) -> int:
    """Add a .cohort manifest to an existing project."""
    from cohort.project_manifest import CohortManifest, load_cohort_settings, MANIFEST_FILENAME

    project_dir = (Path(args.dir) if args.dir else Path.cwd()).resolve()

    if not project_dir.exists():
        print(f"[X] Directory not found: {project_dir}")
        return 1

    manifest_path = project_dir / MANIFEST_FILENAME
    if manifest_path.exists():
        print(f"  [*] Project already linked: {manifest_path}")
        print(f"  [*] Delete .cohort and re-run to reset the manifest.")
        return 0

    cohort_root = Path(__file__).resolve().parent.parent
    settings = load_cohort_settings(cohort_root)

    project_name = args.name or project_dir.name

    print()
    print(f"  Linking project: {project_name}")
    print(f"  Location:        {project_dir}")

    # Housekeeping: .gitignore, .env.example, .env
    print()
    _write_housekeeping(project_dir)

    # Build manifest
    manifest = CohortManifest.create(
        project_dir=project_dir,
        cohort_root=cohort_root,
        project_name=project_name,
        cohort_settings=settings,
    )

    # Review + edit permissions interactively (unless --no-review)
    if not getattr(args, "no_review", False):
        manifest = _review_permissions(manifest, project_dir)

    manifest.write(project_dir)
    print(f"  [OK] Linked {project_dir.name} to Cohort")

    manifest.ensure_working_memory_dir(project_dir)
    print(f"  [OK] Working memory directory: {manifest.working_memory_path}")

    print()

    # Open in VS Code
    if not getattr(args, "no_code", False):
        _open_in_vscode(project_dir)

    print()
    return 0


def _cmd_export_personas(args: argparse.Namespace) -> int:
    """Regenerate .claude/agents/*.md from agents/ configs."""
    from cohort.export_personas import export_all_personas

    cohort_root = Path(__file__).resolve().parent.parent
    print()
    print("Exporting agent personas...")
    print(f"  Source:  {cohort_root / 'agents'}")
    print(f"  Output:  {cohort_root / '.claude' / 'agents'}")
    print()

    ok, fail = export_all_personas(
        cohort_root,
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
    )

    print()
    if fail == 0:
        print(f"  [OK] {ok} persona files written.")
    else:
        print(f"  [!] {ok} succeeded, {fail} failed.")
    return 0 if fail == 0 else 1


# =====================================================================
# Main entry point
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="cohort -- multi-agent orchestration")
    sub = parser.add_subparsers(dest="command")

    # -- gate -----------------------------------------------------------
    gate_parser = sub.add_parser("gate", help="Check if an agent should respond")
    gate_parser.add_argument("--agent", required=True, help="Agent ID to check")
    gate_parser.add_argument("--channel", required=True, help="Channel ID")
    gate_parser.add_argument("--file", required=True, help="Path to conversation .jsonl file")
    gate_parser.add_argument("--agents", required=True, help="Path to agents.json config")
    gate_parser.add_argument(
        "--format", choices=["json", "text"], default="text", help="Output format"
    )

    # -- next-speaker ---------------------------------------------------
    next_parser = sub.add_parser("next-speaker", help="Recommend who should talk next")
    next_parser.add_argument("--channel", required=True, help="Channel ID")
    next_parser.add_argument("--file", required=True, help="Path to conversation .jsonl file")
    next_parser.add_argument("--agents", required=True, help="Path to agents.json config")
    next_parser.add_argument("--top", type=int, default=3, help="Number of speakers to show")
    next_parser.add_argument(
        "--format", choices=["json", "text"], default="text", help="Output format"
    )

    # -- say ------------------------------------------------------------
    say_parser = sub.add_parser("say", help="Append a message to the conversation")
    say_parser.add_argument("--sender", required=True, help="Sender agent ID")
    say_parser.add_argument("--channel", required=True, help="Channel ID")
    say_parser.add_argument("--file", required=True, help="Path to conversation .jsonl file")
    say_parser.add_argument("--message", required=True, help="Message content")

    # -- briefing -------------------------------------------------------
    brief_parser = sub.add_parser("briefing", help="Generate or view executive briefing")
    brief_sub = brief_parser.add_subparsers(dest="brief_command")

    gen_parser = brief_sub.add_parser("generate", help="Generate a new briefing")
    gen_parser.add_argument("--hours", type=int, default=24, help="Hours to cover")
    gen_parser.add_argument("--data-dir", default="data", help="Data directory")
    gen_parser.add_argument("--no-post", action="store_true", help="Don't post to channel")
    gen_parser.add_argument(
        "--format", choices=["json", "text"], default="text", help="Output format"
    )

    latest_parser = brief_sub.add_parser("latest", help="Show latest briefing")
    latest_parser.add_argument("--data-dir", default="data", help="Data directory")
    latest_parser.add_argument(
        "--format", choices=["json", "text"], default="text", help="Output format"
    )

    # -- setup ------------------------------------------------------
    sub.add_parser(
        "setup",
        help="Interactive setup wizard (Ollama + model + content feeds)",
    )

    # -- tutorial ---------------------------------------------------
    sub.add_parser(
        "tutorial",
        help="Interactive walkthrough of Cohort's scoring engine",
    )

    # -- new --------------------------------------------------------
    new_parser = sub.add_parser(
        "new",
        help="Create a new project linked to Cohort (git init + .cohort manifest)",
    )
    new_parser.add_argument("name", help="Project name (used as directory name)")
    new_parser.add_argument(
        "--dir", default=None,
        help="Parent directory to create the project in (default: current directory)",
    )
    new_parser.add_argument(
        "--no-git", action="store_true",
        help="Skip git init",
    )
    new_parser.add_argument(
        "--no-review", action="store_true",
        help="Skip the interactive permission review (use defaults)",
    )
    new_parser.add_argument(
        "--no-code", action="store_true",
        help="Skip opening the project in VS Code",
    )

    # -- link -------------------------------------------------------
    link_parser = sub.add_parser(
        "link",
        help="Link an existing project to Cohort (adds .cohort manifest)",
    )
    link_parser.add_argument(
        "--dir", default=None,
        help="Project directory to link (default: current directory)",
    )
    link_parser.add_argument(
        "--name", default=None,
        help="Override the project name in the manifest",
    )
    link_parser.add_argument(
        "--no-review", action="store_true",
        help="Skip the interactive permission review (use defaults)",
    )
    link_parser.add_argument(
        "--no-code", action="store_true",
        help="Skip opening the project in VS Code",
    )

    # -- export-personas --------------------------------------------
    export_parser = sub.add_parser(
        "export-personas",
        help="Regenerate .claude/agents/*.md persona files from agents/ configs",
    )
    export_parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    export_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")

    # -- serve ------------------------------------------------------
    serve_parser = sub.add_parser(
        "serve",
        help="Start the Cohort HTTP server (API + dashboard)",
    )
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=5100, help="Port (default: 5100)")
    serve_parser.add_argument("--data-dir", default="data", help="Data directory")

    # -- launch -----------------------------------------------------
    launch_parser = sub.add_parser(
        "launch",
        help="Start Cohort with system tray icon (Windows installer mode)",
    )
    launch_parser.add_argument("--port", type=int, default=5100, help="Port (default: 5100)")
    launch_parser.add_argument("--no-browser", action="store_true", help="Don't open browser on startup")
    launch_parser.add_argument("--force-setup", action="store_true", help="Force the setup wizard")

    args = parser.parse_args()

    if args.command == "setup":
        from cohort.local.setup import run_setup

        sys.exit(run_setup())
    elif args.command == "tutorial":
        from cohort.local.tutorial import run_tutorial

        sys.exit(run_tutorial())
    elif args.command == "new":
        sys.exit(_cmd_new(args))
    elif args.command == "link":
        sys.exit(_cmd_link(args))
    elif args.command == "export-personas":
        sys.exit(_cmd_export_personas(args))
    elif args.command == "gate":
        sys.exit(_cmd_gate(args))
    elif args.command == "next-speaker":
        sys.exit(_cmd_next_speaker(args))
    elif args.command == "say":
        sys.exit(_cmd_say(args))
    elif args.command == "briefing":
        sys.exit(_cmd_briefing(args))
    elif args.command == "serve":
        from cohort.server import serve

        serve(host=args.host, port=args.port, data_dir=args.data_dir)
    elif args.command == "launch":
        from cohort.launcher import launch

        sys.exit(launch(
            port=args.port,
            no_browser=args.no_browser,
            force_setup=args.force_setup,
        ))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
