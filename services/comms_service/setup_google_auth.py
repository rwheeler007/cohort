"""
Google Calendar OAuth Setup for BOSS Communications Service.

Run this script once to authorize BOSS to access your Google Calendar.
It will open a browser window for you to sign in and grant permission.
The resulting token is saved locally and reused automatically.

Usage:
    python setup_google_auth.py

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
"""

import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Paths
COHORT_ROOT = Path(__file__).parent.parent.parent
CREDENTIALS_PATH = COHORT_ROOT / "data" / "comms_service" / "config" / "google_credentials.json"
TOKEN_PATH = COHORT_ROOT / "data" / "comms_service" / "config" / "google_tokens.json"

# Scopes - read/write calendar events
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def setup():
    """Run the OAuth flow and save the token."""
    print("[>>] BOSS Google Calendar Authorization")
    print(f"[*]  Credentials: {CREDENTIALS_PATH}")
    print(f"[*]  Token will be saved to: {TOKEN_PATH}")
    print()

    if not CREDENTIALS_PATH.exists():
        print(f"[X] Credentials file not found: {CREDENTIALS_PATH}")
        print("    Download it from Google Cloud Console > APIs & Services > Credentials")
        sys.exit(1)

    creds = None

    # Check for existing token
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            print("[*] Found existing token")
        except Exception as e:
            print(f"[!] Existing token invalid: {e}")
            creds = None

    # Refresh or get new token
    if creds and creds.expired and creds.refresh_token:
        print("[>>] Refreshing expired token...")
        try:
            creds.refresh(Request())
            print("[OK] Token refreshed")
        except Exception as e:
            print(f"[!] Refresh failed: {e}")
            creds = None

    if not creds or not creds.valid:
        print("[>>] Starting OAuth flow - a browser window will open...")
        print("[*]  Sign in with the Google account whose calendar you want to use")
        print()

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH), SCOPES
        )
        creds = flow.run_local_server(port=0)
        print()
        print("[OK] Authorization successful!")

    # Save the token
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"[OK] Token saved to: {TOKEN_PATH}")

    # Test the connection
    print()
    print("[>>] Testing Calendar API connection...")
    try:
        service = build("calendar", "v3", credentials=creds)
        calendar = service.calendars().get(calendarId="primary").execute()
        print(f"[OK] Connected to calendar: {calendar.get('summary', 'Primary')}")

        # List upcoming events as a test
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        events_result = (
            service.events()
            .list(calendarId="primary", timeMin=now, maxResults=5, singleEvents=True, orderBy="startTime")
            .execute()
        )
        events = events_result.get("items", [])
        if events:
            print(f"[OK] Found {len(events)} upcoming event(s):")
            for event in events:
                start = event["start"].get("dateTime", event["start"].get("date"))
                print(f"     - {start}: {event.get('summary', '(no title)')}")
        else:
            print("[*] No upcoming events found")

    except Exception as e:
        print(f"[!] Calendar API test failed: {e}")
        print("    The token was saved - you may need to enable the Calendar API in Google Cloud Console")

    print()
    print("[OK] Setup complete! The comms service can now access your Google Calendar.")


if __name__ == "__main__":
    setup()
