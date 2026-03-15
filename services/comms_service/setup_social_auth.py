"""
Social Media OAuth Setup for BOSS Communications Service.

Run this script to authorize BOSS to post to your social media accounts.
Supports Twitter/X, LinkedIn, Facebook, and Threads.

Usage:
    python setup_social_auth.py --platform twitter
    python setup_social_auth.py --platform linkedin
    python setup_social_auth.py --platform facebook
    python setup_social_auth.py --platform threads
    python setup_social_auth.py --all

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
"""

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

# Paths
BOSS_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = BOSS_ROOT / "data" / "comms_service" / "config"
TOKENS_PATH = CONFIG_PATH / "social_tokens.json"

# OAuth redirect URI (local server)
REDIRECT_URI = "http://localhost:8888/callback"
CALLBACK_RECEIVED = False
CALLBACK_CODE = None
CALLBACK_STATE = None


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callbacks."""

    def do_GET(self):
        global CALLBACK_RECEIVED, CALLBACK_CODE, CALLBACK_STATE

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/callback":
            CALLBACK_CODE = query.get("code", [None])[0]
            CALLBACK_STATE = query.get("state", [None])[0]
            CALLBACK_RECEIVED = True

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()

            if CALLBACK_CODE:
                self.wfile.write(b"<html><body><h1>[OK] Authorization successful!</h1>")
                self.wfile.write(b"<p>You can close this window and return to the terminal.</p>")
                self.wfile.write(b"</body></html>")
            else:
                error = query.get("error", ["Unknown error"])[0]
                self.wfile.write(b"<html><body><h1>[X] Authorization failed</h1>")
                self.wfile.write(f"<p>Error: {error}</p>".encode())
                self.wfile.write(b"</body></html>")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress server logs."""
        pass


def load_tokens():
    """Load existing OAuth tokens."""
    if not TOKENS_PATH.exists():
        return {}

    try:
        with open(TOKENS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[!] Failed to load tokens: {exc}")
        return {}


def save_tokens(tokens):
    """Save OAuth tokens to disk."""
    CONFIG_PATH.mkdir(parents=True, exist_ok=True)

    try:
        with open(TOKENS_PATH, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        print(f"[OK] Tokens saved to: {TOKENS_PATH}")
    except Exception as exc:
        print(f"[X] Failed to save tokens: {exc}")
        sys.exit(1)


def setup_twitter():
    """Setup Twitter OAuth."""
    print("\n[>>] Twitter/X OAuth Setup")
    print("[*]  This will authorize BOSS to post tweets on your behalf")
    print()

    client_id = os.getenv("TWITTER_CLIENT_ID")
    client_secret = os.getenv("TWITTER_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("[X] Twitter credentials not found in .env file")
        print("    Set TWITTER_CLIENT_ID and TWITTER_CLIENT_SECRET")
        return

    # Start local server for callback
    server = HTTPServer(("localhost", 8888), CallbackHandler)
    print(f"[*]  Callback server running at {REDIRECT_URI}")

    # Build authorization URL
    auth_url = "https://twitter.com/i/oauth2/authorize?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "tweet.read tweet.write users.read offline.access",
        "state": "twitter_oauth_state",
        "code_challenge": "challenge",
        "code_challenge_method": "plain",
    })

    print(f"[>>] Opening browser for authorization...")
    print(f"[*]  If browser doesn't open, visit: {auth_url}")
    print()
    webbrowser.open(auth_url)

    # Wait for callback
    global CALLBACK_RECEIVED, CALLBACK_CODE
    while not CALLBACK_RECEIVED:
        server.handle_request()

    server.server_close()

    if not CALLBACK_CODE:
        print("[X] Authorization failed - no code received")
        return

    print("[*]  Authorization code received, exchanging for token...")

    # Exchange code for token
    token_url = "https://api.twitter.com/2/oauth2/token"
    token_data = {
        "code": CALLBACK_CODE,
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": "challenge",
    }

    try:
        with httpx.Client() as client:
            response = client.post(
                token_url,
                data=token_data,
                auth=(client_id, client_secret),
            )
            response.raise_for_status()
            token_response = response.json()

            # Save token
            tokens = load_tokens()
            tokens["twitter"] = {
                "access_token": token_response["access_token"],
                "refresh_token": token_response.get("refresh_token"),
                "expires_at": (datetime.utcnow() + timedelta(seconds=token_response.get("expires_in", 7200))).isoformat(),
                "token_type": token_response["token_type"],
            }
            save_tokens(tokens)

            print("[OK] Twitter authorization successful!")

    except httpx.HTTPStatusError as exc:
        print(f"[X] Token exchange failed: {exc.response.status_code}")
        print(f"    {exc.response.text}")
    except Exception as exc:
        print(f"[X] Token exchange failed: {exc}")


def setup_linkedin():
    """Setup LinkedIn OAuth."""
    print("\n[>>] LinkedIn OAuth Setup")
    print("[*]  This will authorize BOSS to post on your LinkedIn profile")
    print()

    client_id = os.getenv("LINKEDIN_CLIENT_ID")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("[X] LinkedIn credentials not found in .env file")
        print("    Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET")
        return

    # Start local server for callback
    server = HTTPServer(("localhost", 8888), CallbackHandler)
    print(f"[*]  Callback server running at {REDIRECT_URI}")

    # Build authorization URL
    auth_url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "w_member_social r_liteprofile r_emailaddress",
        "state": "linkedin_oauth_state",
    })

    print(f"[>>] Opening browser for authorization...")
    print(f"[*]  If browser doesn't open, visit: {auth_url}")
    print()
    webbrowser.open(auth_url)

    # Wait for callback
    global CALLBACK_RECEIVED, CALLBACK_CODE
    CALLBACK_RECEIVED = False
    CALLBACK_CODE = None

    while not CALLBACK_RECEIVED:
        server.handle_request()

    server.server_close()

    if not CALLBACK_CODE:
        print("[X] Authorization failed - no code received")
        return

    print("[*]  Authorization code received, exchanging for token...")

    # Exchange code for token
    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    token_data = {
        "grant_type": "authorization_code",
        "code": CALLBACK_CODE,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        with httpx.Client() as client:
            response = client.post(token_url, data=token_data)
            response.raise_for_status()
            token_response = response.json()

            # Get user profile to get person URN
            me_url = "https://api.linkedin.com/v2/me"
            me_response = client.get(
                me_url,
                headers={"Authorization": f"Bearer {token_response['access_token']}"}
            )
            me_response.raise_for_status()
            profile = me_response.json()

            # Save token
            tokens = load_tokens()
            tokens["linkedin"] = {
                "access_token": token_response["access_token"],
                "expires_at": (datetime.utcnow() + timedelta(seconds=token_response.get("expires_in", 5184000))).isoformat(),
                "person_urn": f"urn:li:person:{profile['id']}",
            }
            save_tokens(tokens)

            print("[OK] LinkedIn authorization successful!")
            print(f"[*]  Authorized as: {profile.get('localizedFirstName', '')} {profile.get('localizedLastName', '')}")

    except httpx.HTTPStatusError as exc:
        print(f"[X] Token exchange failed: {exc.response.status_code}")
        print(f"    {exc.response.text}")
    except Exception as exc:
        print(f"[X] Token exchange failed: {exc}")


def setup_facebook():
    """Setup Facebook OAuth."""
    print("\n[>>] Facebook OAuth Setup")
    print("[*]  This will authorize BOSS to post on your Facebook page")
    print()

    app_id = os.getenv("FACEBOOK_APP_ID")
    app_secret = os.getenv("FACEBOOK_APP_SECRET")

    if not app_id or not app_secret:
        print("[X] Facebook credentials not found in .env file")
        print("    Set FACEBOOK_APP_ID and FACEBOOK_APP_SECRET")
        return

    # Start local server for callback
    server = HTTPServer(("localhost", 8888), CallbackHandler)
    print(f"[*]  Callback server running at {REDIRECT_URI}")

    # Build authorization URL
    auth_url = "https://www.facebook.com/v18.0/dialog/oauth?" + urlencode({
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "pages_manage_posts,pages_read_engagement,pages_show_list",
        "state": "facebook_oauth_state",
    })

    print(f"[>>] Opening browser for authorization...")
    print(f"[*]  If browser doesn't open, visit: {auth_url}")
    print()
    webbrowser.open(auth_url)

    # Wait for callback
    global CALLBACK_RECEIVED, CALLBACK_CODE
    CALLBACK_RECEIVED = False
    CALLBACK_CODE = None

    while not CALLBACK_RECEIVED:
        server.handle_request()

    server.server_close()

    if not CALLBACK_CODE:
        print("[X] Authorization failed - no code received")
        return

    print("[*]  Authorization code received, exchanging for token...")

    # Exchange code for token
    token_url = "https://graph.facebook.com/v18.0/oauth/access_token?" + urlencode({
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "client_secret": app_secret,
        "code": CALLBACK_CODE,
    })

    try:
        with httpx.Client() as client:
            response = client.get(token_url)
            response.raise_for_status()
            token_response = response.json()

            # Get user's pages
            pages_url = f"https://graph.facebook.com/v18.0/me/accounts?access_token={token_response['access_token']}"
            pages_response = client.get(pages_url)
            pages_response.raise_for_status()
            pages_data = pages_response.json()

            pages = pages_data.get("data", [])
            if not pages:
                print("[X] No Facebook pages found for this account")
                print("    You need to manage at least one Facebook page to post")
                return

            # Show pages and let user choose
            print("\n[*]  Available Facebook pages:")
            for i, page in enumerate(pages):
                print(f"     {i + 1}. {page['name']} (ID: {page['id']})")

            choice = input("\n[?]  Select page number: ").strip()
            try:
                page_index = int(choice) - 1
                selected_page = pages[page_index]
            except (ValueError, IndexError):
                print("[X] Invalid selection")
                return

            # Save token
            tokens = load_tokens()
            tokens["facebook"] = {
                "access_token": selected_page["access_token"],  # Use page token, not user token
                "page_id": selected_page["id"],
                "page_name": selected_page["name"],
            }
            save_tokens(tokens)

            print(f"[OK] Facebook authorization successful!")
            print(f"[*]  Authorized to post to: {selected_page['name']}")

    except httpx.HTTPStatusError as exc:
        print(f"[X] Token exchange failed: {exc.response.status_code}")
        print(f"    {exc.response.text}")
    except Exception as exc:
        print(f"[X] Token exchange failed: {exc}")


def setup_threads():
    """Setup Threads OAuth."""
    print("\n[>>] Threads OAuth Setup")
    print("[*]  Threads uses the Instagram Graph API")
    print("[*]  You need an Instagram Professional or Creator account")
    print()

    app_id = os.getenv("THREADS_APP_ID")
    app_secret = os.getenv("THREADS_APP_SECRET")

    if not app_id or not app_secret:
        print("[X] Threads credentials not found in .env file")
        print("    Set THREADS_APP_ID and THREADS_APP_SECRET")
        return

    print("[!] Threads OAuth requires additional Instagram Graph API setup")
    print("    Visit: https://developers.facebook.com/docs/threads/get-started")
    print("    Follow the setup guide to connect your Instagram account")
    print()
    print("[*]  Once configured, you'll need to manually add your Threads token")
    print(f"[*]  Add to {TOKENS_PATH} with this format:")
    print()
    print('    "threads": {')
    print('        "access_token": "YOUR_THREADS_ACCESS_TOKEN",')
    print('        "user_id": "YOUR_INSTAGRAM_USER_ID",')
    print('        "username": "your_instagram_username"')
    print('    }')


def main():
    parser = argparse.ArgumentParser(description="Setup social media OAuth for BOSS")
    parser.add_argument(
        "--platform",
        choices=["twitter", "linkedin", "facebook", "threads"],
        help="Platform to authorize",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Setup all platforms (skip Threads - manual setup required)",
    )

    args = parser.parse_args()

    # Load environment
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print("[OK] Loaded .env file")
    else:
        print("[!] No .env file found - using environment variables")

    print("\n[>>] BOSS Social Media OAuth Setup")
    print(f"[*]  Tokens will be saved to: {TOKENS_PATH}")
    print()

    if args.all:
        setup_twitter()
        setup_linkedin()
        setup_facebook()
        setup_threads()
    elif args.platform == "twitter":
        setup_twitter()
    elif args.platform == "linkedin":
        setup_linkedin()
    elif args.platform == "facebook":
        setup_facebook()
    elif args.platform == "threads":
        setup_threads()
    else:
        parser.print_help()

    print("\n[OK] Setup complete!")
    print(f"[*]  Tokens saved to: {TOKENS_PATH}")
    print("[!]  DO NOT commit this file to git - it contains sensitive credentials")


if __name__ == "__main__":
    main()
