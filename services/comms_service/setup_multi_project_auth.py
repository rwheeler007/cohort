"""
Setup Multi-Project Authentication for BOSS Communications Service.

This script helps configure separate Google Calendar and social media OAuth
credentials for different projects (ChillGuard, PartSpec, Patent, etc.).

Usage:
    # Setup Google Calendar for a specific project
    python setup_multi_project_auth.py calendar --project chillguard

    # Setup social media for a specific project
    python setup_multi_project_auth.py social --project partspec --platform twitter

    # List all projects and their status
    python setup_multi_project_auth.py list

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
"""

import argparse
import sys
from pathlib import Path

# Add comms_service to path
COMMS_DIR = Path(__file__).parent
sys.path.insert(0, str(COMMS_DIR))

from project_settings import ProjectSettingsManager

COHORT_ROOT = COMMS_DIR.parent.parent


def setup_calendar(project_id: str):
    """Setup Google Calendar OAuth for a project."""
    print(f"[>>] Setting up Google Calendar for project: {project_id}")

    project_settings = ProjectSettingsManager(COHORT_ROOT)
    project = project_settings.get_project(project_id)

    if not project:
        print(f"[X] Project not found: {project_id}")
        print("[!] Available projects:", ", ".join([p.project_id for p in project_settings.list_projects()]))
        return 1

    if not project.calendar:
        print(f"[X] Calendar not configured for project: {project_id}")
        return 1

    paths = project_settings.get_calendar_paths(project_id)
    if not paths:
        print(f"[X] Could not get calendar paths for project: {project_id}")
        return 1

    credentials_path = paths["credentials"]
    tokens_path = paths["tokens"]

    print(f"\n[*] Credentials file: {credentials_path}")
    print(f"[*] Tokens file: {tokens_path}")

    if not credentials_path.exists():
        print("\n[!] Credentials file not found!")
        print(f"[>>] To set up Google Calendar for {project_id}:")
        print("    1. Go to https://console.cloud.google.com/")
        print("    2. Create or select a project")
        print("    3. Enable Google Calendar API")
        print("    4. Create OAuth 2.0 credentials (Desktop app)")
        print("    5. Download credentials JSON")
        print(f"    6. Save to: {credentials_path}")
        print("\n[>>] Then run this script again.")
        return 1

    # Import Google Calendar setup
    try:
        from setup_google_auth import setup_google_calendar
    except ImportError:
        print("[X] setup_google_auth.py not found")
        return 1

    print("\n[>>] Running OAuth flow...")
    print("[*] A browser window will open for authorization")

    success = setup_google_calendar(
        credentials_path=str(credentials_path),
        token_path=str(tokens_path)
    )

    if success:
        print(f"\n[OK] Google Calendar configured for project: {project_id}")
        print(f"[*] Calendar account will be used for {project.display_name} events")

        # Enable calendar for this project
        project_settings.set_calendar_enabled(project_id, True)
        print(f"[OK] Calendar enabled for project: {project_id}")
        return 0
    else:
        print(f"\n[X] Failed to configure Google Calendar for project: {project_id}")
        return 1


def setup_social(project_id: str, platform: str):
    """Setup social media OAuth for a project and platform."""
    print(f"[>>] Setting up {platform} for project: {project_id}")

    project_settings = ProjectSettingsManager(COHORT_ROOT)
    project = project_settings.get_project(project_id)

    if not project:
        print(f"[X] Project not found: {project_id}")
        print("[!] Available projects:", ", ".join([p.project_id for p in project_settings.list_projects()]))
        return 1

    if not project.social:
        print(f"[X] Social media not configured for project: {project_id}")
        return 1

    # Import social media setup
    try:
        from setup_social_auth import setup_platform_oauth
    except ImportError:
        print("[X] setup_social_auth.py not found")
        return 1

    print(f"\n[>>] Running OAuth flow for {platform}...")
    print(f"[*] Follow the prompts to authorize {project.display_name}")

    token_data = setup_platform_oauth(platform)

    if token_data:
        # Save token to project
        project_settings.update_social_platform_token(project_id, platform, token_data)
        print(f"\n[OK] {platform} configured for project: {project_id}")
        print(f"[*] Account will be used for {project.display_name} posts")

        # Enable social for this project if not already
        if not project.social.enabled:
            project_settings.set_social_enabled(project_id, True)
            print(f"[OK] Social media enabled for project: {project_id}")
        return 0
    else:
        print(f"\n[X] Failed to configure {platform} for project: {project_id}")
        return 1


def list_projects():
    """List all projects and their configuration status."""
    project_settings = ProjectSettingsManager(COHORT_ROOT)
    projects = project_settings.list_projects()

    print("\n[*] Configured Projects:")
    print("=" * 70)

    for project in projects:
        print(f"\n{project.display_name} ({project.project_id})")
        print(f"  Color: {project.color}")

        # Calendar status
        if project.calendar:
            enabled_str = "[ENABLED]" if project.calendar.enabled else "[DISABLED]"
            paths = project_settings.get_calendar_paths(project.project_id)
            has_creds = paths and paths["credentials"].exists()
            has_token = paths and paths["tokens"].exists()

            print(f"  Calendar: {enabled_str}")
            print(f"    Credentials: {'[OK]' if has_creds else '[MISSING]'}")
            print(f"    Token: {'[OK]' if has_token else '[MISSING]'}")
        else:
            print("  Calendar: [NOT CONFIGURED]")

        # Social status
        if project.social:
            enabled_str = "[ENABLED]" if project.social.enabled else "[DISABLED]"
            print(f"  Social Media: {enabled_str}")

            platforms = ["twitter", "linkedin", "facebook", "threads", "reddit"]
            for platform in platforms:
                token = project.social.platforms.get(platform)
                if token:
                    username = token.get("username", "Unknown")
                    print(f"    {platform}: [OK] ({username})")
                else:
                    print(f"    {platform}: [NOT CONNECTED]")
        else:
            print("  Social Media: [NOT CONFIGURED]")

    print("\n" + "=" * 70)
    print(f"\n[*] Total projects: {len(projects)}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Setup multi-project authentication for BOSS Communications Service"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Calendar setup
    calendar_parser = subparsers.add_parser("calendar", help="Setup Google Calendar for a project")
    calendar_parser.add_argument("--project", required=True, help="Project ID (e.g., chillguard, partspec)")

    # Social media setup
    social_parser = subparsers.add_parser("social", help="Setup social media for a project")
    social_parser.add_argument("--project", required=True, help="Project ID (e.g., chillguard, partspec)")
    social_parser.add_argument(
        "--platform",
        required=True,
        choices=["twitter", "linkedin", "facebook", "threads", "reddit"],
        help="Social media platform"
    )

    # List projects
    subparsers.add_parser("list", help="List all projects and their status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "calendar":
        return setup_calendar(args.project)
    elif args.command == "social":
        return setup_social(args.project, args.platform)
    elif args.command == "list":
        return list_projects()
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
