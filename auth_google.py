"""
auth_google.py - First-time OAuth2 setup for Gmail and Google Calendar.

Run this script once to authenticate with Google and generate token files.
After authentication, tokens auto-refresh — no re-authentication needed.

Usage:
    python auth_google.py
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv

# Load config
ENV_PATH = os.path.join(os.path.dirname(__file__), "config.env")
load_dotenv(ENV_PATH)
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(DATA_DIR, exist_ok=True)

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")

# Scopes for both Gmail and Calendar
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

GMAIL_TOKEN_PATH = os.path.join(DATA_DIR, "gmail_token.json")
CALENDAR_TOKEN_PATH = os.path.join(DATA_DIR, "calendar_token.json")


def authenticate(scopes: list[str], token_path: str, service_name: str) -> Credentials:
    """Run the OAuth2 flow for a given set of scopes and save the token."""
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"\nERROR: {CREDENTIALS_FILE} not found!")
        print("Download it from Google Cloud Console:")
        print("  1. Go to https://console.cloud.google.com")
        print("  2. Select your project")
        print("  3. Go to APIs & Services > Credentials")
        print("  4. Download the OAuth 2.0 Client ID as credentials.json")
        print(f"  5. Place it at: {CREDENTIALS_FILE}")
        sys.exit(1)

    print(f"\n--- Authenticating {service_name} ---")
    print("A browser window will open. Sign in with your Google account")
    print("and grant the requested permissions.\n")

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, scopes)
    creds = flow.run_local_server(port=0)

    # Save the token
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"{service_name} authenticated successfully!")
    print(f"Token saved to: {token_path}")
    return creds


def main():
    print("=" * 50)
    print("  OpenPaw — Google Services Authentication")
    print("=" * 50)

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"\nERROR: credentials.json not found at {CREDENTIALS_FILE}")
        print("\nTo set up Google API credentials:")
        print("  1. Go to https://console.cloud.google.com")
        print("  2. Create a new project (or select existing)")
        print("  3. Enable the Gmail API and Google Calendar API")
        print("  4. Go to APIs & Services > Credentials")
        print("  5. Create OAuth 2.0 Client ID (Desktop application)")
        print("  6. Download the JSON and save it as credentials.json")
        print(f"     in: {os.path.dirname(CREDENTIALS_FILE)}")
        sys.exit(1)

    # Check if tokens already exist
    gmail_exists = os.path.exists(GMAIL_TOKEN_PATH)
    calendar_exists = os.path.exists(CALENDAR_TOKEN_PATH)

    if gmail_exists and calendar_exists:
        print("\nBoth tokens already exist:")
        print(f"  Gmail:    {GMAIL_TOKEN_PATH}")
        print(f"  Calendar: {CALENDAR_TOKEN_PATH}")
        response = input("\nRe-authenticate? (yes/no): ").strip().lower()
        if response not in ("yes", "y"):
            print("Aborted.")
            return

    # Authenticate Gmail
    if not gmail_exists or response in ("yes", "y") if "response" in dir() else True:
        authenticate(GMAIL_SCOPES, GMAIL_TOKEN_PATH, "Gmail")

    # Authenticate Calendar
    if not calendar_exists or response in ("yes", "y") if "response" in dir() else True:
        authenticate(CALENDAR_SCOPES, CALENDAR_TOKEN_PATH, "Google Calendar")

    print("\n" + "=" * 50)
    print("  Authentication complete!")
    print("=" * 50)
    print(f"\n  Gmail token:    {GMAIL_TOKEN_PATH}")
    print(f"  Calendar token: {CALENDAR_TOKEN_PATH}")
    print("\nYou can now start OpenPaw with: python main.py")


if __name__ == "__main__":
    main()
