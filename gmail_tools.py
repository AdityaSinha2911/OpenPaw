"""
gmail_tools.py - Gmail API integration for OpenPaw.

Provides full Gmail control: read, search, send, reply, summarize,
label management, and email organization via the Gmail API v1.
"""

import asyncio
import base64
import logging
import os
from datetime import datetime
from email.mime.text import MIMEText
from functools import partial

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("openpaw.gmail")

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


class GmailTools:
    """Gmail API wrapper with async-compatible methods."""

    def __init__(self, data_dir: str, ollama_connector=None):
        self.data_dir = data_dir
        self.ollama = ollama_connector
        self._token_path = os.path.join(data_dir, "gmail_token.json")
        self._service = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def _get_service(self):
        """Build or return a cached Gmail API service, refreshing tokens as needed."""
        if self._service is not None:
            return self._service

        if not os.path.exists(self._token_path):
            raise FileNotFoundError(
                "Gmail token not found. Please run: python auth_google.py"
            )

        creds = Credentials.from_authorized_user_file(self._token_path, GMAIL_SCOPES)

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(self._token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                logger.info("Gmail token refreshed successfully")
            except Exception as exc:
                logger.error("Failed to refresh Gmail token: %s", exc)
                raise RuntimeError(
                    "Gmail token expired and could not be refreshed. "
                    "Please run: python auth_google.py"
                ) from exc

        if not creds.valid:
            raise RuntimeError(
                "Gmail credentials are invalid. Please run: python auth_google.py"
            )

        self._service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail API service initialized")
        return self._service

    def is_authenticated(self) -> bool:
        """Check if Gmail is authenticated and tokens are valid."""
        try:
            self._get_service()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous function in the default executor."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, partial(func, *args, **kwargs))

    @staticmethod
    def _decode_body(payload: dict) -> str:
        """Extract plain text body from a Gmail message payload."""
        # Direct body
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        # Multipart — look for text/plain first, then text/html
        parts = payload.get("parts", [])
        text_part = None
        html_part = None

        for part in parts:
            mime = part.get("mimeType", "")
            if mime == "text/plain" and part.get("body", {}).get("data"):
                text_part = part
            elif mime == "text/html" and part.get("body", {}).get("data"):
                html_part = part
            # Nested multipart
            elif mime.startswith("multipart/") and part.get("parts"):
                for sub in part["parts"]:
                    sub_mime = sub.get("mimeType", "")
                    if sub_mime == "text/plain" and sub.get("body", {}).get("data"):
                        text_part = sub
                    elif sub_mime == "text/html" and sub.get("body", {}).get("data"):
                        html_part = sub

        chosen = text_part or html_part
        if chosen and chosen.get("body", {}).get("data"):
            raw = base64.urlsafe_b64decode(chosen["body"]["data"]).decode("utf-8", errors="replace")
            # Strip HTML tags for a cleaner text output if it was HTML
            if chosen.get("mimeType") == "text/html":
                import re
                raw = re.sub(r"<[^>]+>", "", raw)
                raw = re.sub(r"\s+", " ", raw).strip()
            return raw

        return "(No readable body)"

    @staticmethod
    def _get_header(headers: list[dict], name: str) -> str:
        """Get a header value by name from a list of header dicts."""
        for h in headers:
            if h.get("name", "").lower() == name.lower():
                return h.get("value", "")
        return ""

    @staticmethod
    def _format_date(date_str: str) -> str:
        """Format an email date string to a readable format."""
        try:
            # Gmail dates can have timezone info — parse the main part
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return date_str

    # ------------------------------------------------------------------
    # Read emails
    # ------------------------------------------------------------------
    def _read_emails_sync(self, count: int = 5, query: str = "") -> str:
        """Fetch latest N emails (synchronous)."""
        try:
            service = self._get_service()
            kwargs = {"userId": "me", "maxResults": count}
            if query:
                kwargs["q"] = query

            results = service.users().messages().list(**kwargs).execute()
            messages = results.get("messages", [])

            if not messages:
                return "No emails found."

            output = []
            for i, msg_ref in enumerate(messages):
                msg = service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()

                headers = msg.get("payload", {}).get("headers", [])
                sender = self._get_header(headers, "From")
                subject = self._get_header(headers, "Subject") or "(No subject)"
                date = self._format_date(self._get_header(headers, "Date"))
                snippet = msg.get("snippet", "")[:200]
                msg_id = msg["id"]
                labels = msg.get("labelIds", [])
                unread = "UNREAD" in labels

                status = "[UNREAD] " if unread else ""
                output.append(
                    f"{i + 1}. {status}From: {sender}\n"
                    f"   Subject: {subject}\n"
                    f"   Date: {date}\n"
                    f"   Preview: {snippet}\n"
                    f"   ID: {msg_id}"
                )

            return "Latest emails:\n\n" + "\n\n".join(output)

        except HttpError as exc:
            logger.error("Gmail API error: %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error reading emails: %s", exc)
            return f"Error reading emails: {exc}"

    async def read_emails(self, count: int = 5) -> str:
        """Fetch latest N emails from inbox."""
        return await self._run_sync(self._read_emails_sync, count)

    # ------------------------------------------------------------------
    # Read unread emails
    # ------------------------------------------------------------------
    def _read_unread_sync(self) -> str:
        """Fetch all unread emails (synchronous)."""
        return self._read_emails_sync(count=20, query="is:unread")

    async def read_unread(self) -> str:
        """Read all unread emails."""
        return await self._run_sync(self._read_unread_sync)

    # ------------------------------------------------------------------
    # Search emails
    # ------------------------------------------------------------------
    async def search_emails(self, query: str) -> str:
        """Search emails by query string."""
        return await self._run_sync(self._read_emails_sync, 10, query)

    # ------------------------------------------------------------------
    # Read full email body
    # ------------------------------------------------------------------
    def _read_full_sync(self, message_id: str) -> str:
        """Fetch full email body by message ID (synchronous)."""
        try:
            service = self._get_service()
            msg = service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()

            headers = msg.get("payload", {}).get("headers", [])
            sender = self._get_header(headers, "From")
            subject = self._get_header(headers, "Subject") or "(No subject)"
            date = self._format_date(self._get_header(headers, "Date"))
            to = self._get_header(headers, "To")
            body = self._decode_body(msg.get("payload", {}))

            # Truncate very long bodies
            if len(body) > 3000:
                body = body[:3000] + "\n... (truncated)"

            return (
                f"From: {sender}\n"
                f"To: {to}\n"
                f"Subject: {subject}\n"
                f"Date: {date}\n"
                f"{'=' * 40}\n"
                f"{body}"
            )

        except HttpError as exc:
            logger.error("Gmail API error (full read): %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error reading full email: %s", exc)
            return f"Error reading full email: {exc}"

    async def read_full(self, message_id: str) -> str:
        """Fetch full email body by message ID."""
        return await self._run_sync(self._read_full_sync, message_id)

    # ------------------------------------------------------------------
    # Summarize emails
    # ------------------------------------------------------------------
    async def summarize_emails(self, count: int = 5) -> str:
        """Fetch latest N emails and summarize each with Ollama."""
        if not self.ollama:
            return "Ollama connector is not available for summarization."

        try:
            service = self._get_service()
            results = await self._run_sync(
                lambda: service.users().messages().list(
                    userId="me", maxResults=count
                ).execute()
            )
            messages = results.get("messages", [])

            if not messages:
                return "No emails to summarize."

            summaries = []
            for i, msg_ref in enumerate(messages):
                msg = await self._run_sync(
                    lambda mid=msg_ref["id"]: service.users().messages().get(
                        userId="me", id=mid, format="full"
                    ).execute()
                )

                headers = msg.get("payload", {}).get("headers", [])
                sender = self._get_header(headers, "From")
                subject = self._get_header(headers, "Subject") or "(No subject)"
                body = self._decode_body(msg.get("payload", {}))[:1500]

                # Use Ollama to summarize
                prompt = (
                    f"Summarize this email in 2-3 short lines:\n"
                    f"From: {sender}\nSubject: {subject}\n\n{body}"
                )
                summary = self.ollama.chat(
                    [{"role": "user", "content": prompt}]
                )

                summaries.append(
                    f"{i + 1}. From: {sender}\n"
                    f"   Subject: {subject}\n"
                    f"   Summary: {summary}"
                )

            return "Email Digest:\n\n" + "\n\n".join(summaries)

        except Exception as exc:
            logger.error("Error summarizing emails: %s", exc)
            return f"Error summarizing emails: {exc}"

    # ------------------------------------------------------------------
    # Send email
    # ------------------------------------------------------------------
    def _send_email_sync(self, to: str, subject: str, body: str) -> str:
        """Send a plain text email (synchronous)."""
        try:
            service = self._get_service()
            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
            send_body = {"raw": raw}

            service.users().messages().send(
                userId="me", body=send_body
            ).execute()

            logger.info("Email sent to %s, subject: %s", to, subject)
            return f"Email sent to {to} with subject '{subject}'."

        except HttpError as exc:
            logger.error("Gmail API error (send): %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error sending email: %s", exc)
            return f"Error sending email: {exc}"

    async def send_email(self, to: str, subject: str, body: str) -> str:
        """Send a plain text email."""
        return await self._run_sync(self._send_email_sync, to, subject, body)

    # ------------------------------------------------------------------
    # Reply to email
    # ------------------------------------------------------------------
    def _reply_email_sync(self, message_id: str, body: str) -> str:
        """Reply to an existing email thread (synchronous)."""
        try:
            service = self._get_service()

            # Get the original message to extract thread info
            original = service.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["From", "Subject", "Message-ID"],
            ).execute()

            headers = original.get("payload", {}).get("headers", [])
            original_from = self._get_header(headers, "From")
            original_subject = self._get_header(headers, "Subject")
            original_msg_id = self._get_header(headers, "Message-ID")
            thread_id = original.get("threadId", "")

            # Build reply subject
            if not original_subject.lower().startswith("re:"):
                reply_subject = f"Re: {original_subject}"
            else:
                reply_subject = original_subject

            # Create reply message
            message = MIMEText(body)
            message["to"] = original_from
            message["subject"] = reply_subject
            if original_msg_id:
                message["In-Reply-To"] = original_msg_id
                message["References"] = original_msg_id

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
            send_body = {"raw": raw, "threadId": thread_id}

            service.users().messages().send(
                userId="me", body=send_body
            ).execute()

            logger.info("Reply sent to %s in thread %s", original_from, thread_id)
            return f"Reply sent to {original_from} (subject: {reply_subject})."

        except HttpError as exc:
            logger.error("Gmail API error (reply): %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error replying to email: %s", exc)
            return f"Error replying to email: {exc}"

    async def reply_email(self, message_id: str, body: str) -> str:
        """Reply to an existing email thread."""
        return await self._run_sync(self._reply_email_sync, message_id, body)

    # ------------------------------------------------------------------
    # Mark as read / unread
    # ------------------------------------------------------------------
    def _mark_read_sync(self, message_id: str) -> str:
        """Mark an email as read (synchronous)."""
        try:
            service = self._get_service()
            service.users().messages().modify(
                userId="me", id=message_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            logger.info("Marked email %s as read", message_id)
            return f"Email {message_id} marked as read."
        except HttpError as exc:
            logger.error("Gmail API error (mark read): %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error marking email as read: %s", exc)
            return f"Error: {exc}"

    async def mark_read(self, message_id: str) -> str:
        """Mark an email as read."""
        return await self._run_sync(self._mark_read_sync, message_id)

    def _mark_unread_sync(self, message_id: str) -> str:
        """Mark an email as unread (synchronous)."""
        try:
            service = self._get_service()
            service.users().messages().modify(
                userId="me", id=message_id,
                body={"addLabelIds": ["UNREAD"]}
            ).execute()
            logger.info("Marked email %s as unread", message_id)
            return f"Email {message_id} marked as unread."
        except HttpError as exc:
            logger.error("Gmail API error (mark unread): %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error marking email as unread: %s", exc)
            return f"Error: {exc}"

    async def mark_unread(self, message_id: str) -> str:
        """Mark an email as unread."""
        return await self._run_sync(self._mark_unread_sync, message_id)

    # ------------------------------------------------------------------
    # Archive email
    # ------------------------------------------------------------------
    def _archive_sync(self, message_id: str) -> str:
        """Archive an email by removing INBOX label (synchronous)."""
        try:
            service = self._get_service()
            service.users().messages().modify(
                userId="me", id=message_id,
                body={"removeLabelIds": ["INBOX"]}
            ).execute()
            logger.info("Archived email %s", message_id)
            return f"Email {message_id} archived."
        except HttpError as exc:
            logger.error("Gmail API error (archive): %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error archiving email: %s", exc)
            return f"Error: {exc}"

    async def archive(self, message_id: str) -> str:
        """Archive an email."""
        return await self._run_sync(self._archive_sync, message_id)

    # ------------------------------------------------------------------
    # Delete email
    # ------------------------------------------------------------------
    def _delete_sync(self, message_id: str) -> str:
        """Move an email to trash (synchronous)."""
        try:
            service = self._get_service()
            service.users().messages().trash(
                userId="me", id=message_id
            ).execute()
            logger.info("Deleted (trashed) email %s", message_id)
            return f"Email {message_id} moved to trash."
        except HttpError as exc:
            logger.error("Gmail API error (delete): %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error deleting email: %s", exc)
            return f"Error: {exc}"

    async def delete_email(self, message_id: str) -> str:
        """Move an email to trash."""
        return await self._run_sync(self._delete_sync, message_id)

    # ------------------------------------------------------------------
    # Add / remove labels
    # ------------------------------------------------------------------
    def _modify_labels_sync(self, message_id: str, add: list[str] = None,
                            remove: list[str] = None) -> str:
        """Add or remove labels on an email (synchronous)."""
        try:
            service = self._get_service()
            body = {}
            if add:
                body["addLabelIds"] = add
            if remove:
                body["removeLabelIds"] = remove

            service.users().messages().modify(
                userId="me", id=message_id, body=body
            ).execute()

            actions = []
            if add:
                actions.append(f"added labels: {', '.join(add)}")
            if remove:
                actions.append(f"removed labels: {', '.join(remove)}")

            result = f"Email {message_id}: {'; '.join(actions)}."
            logger.info(result)
            return result

        except HttpError as exc:
            logger.error("Gmail API error (labels): %s", exc)
            return f"Gmail API error: {exc}"
        except Exception as exc:
            logger.error("Error modifying labels: %s", exc)
            return f"Error: {exc}"

    async def modify_labels(self, message_id: str, add: list[str] = None,
                            remove: list[str] = None) -> str:
        """Add or remove labels on an email."""
        return await self._run_sync(self._modify_labels_sync, message_id, add, remove)

    # ------------------------------------------------------------------
    # Unread count (used by scheduler)
    # ------------------------------------------------------------------
    def _get_unread_count_sync(self) -> int:
        """Get the count of unread emails (synchronous)."""
        try:
            service = self._get_service()
            results = service.users().messages().list(
                userId="me", q="is:unread", maxResults=1
            ).execute()
            return results.get("resultSizeEstimate", 0)
        except Exception as exc:
            logger.error("Error getting unread count: %s", exc)
            return 0

    async def get_unread_count(self) -> int:
        """Get the count of unread emails."""
        return await self._run_sync(self._get_unread_count_sync)

    # ------------------------------------------------------------------
    # Get top N email summaries (used by scheduler for briefing)
    # ------------------------------------------------------------------
    def _get_top_emails_sync(self, count: int = 3) -> list[dict]:
        """Get top N emails as structured data (synchronous)."""
        try:
            service = self._get_service()
            results = service.users().messages().list(
                userId="me", maxResults=count
            ).execute()
            messages = results.get("messages", [])

            emails = []
            for msg_ref in messages:
                msg = service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="metadata",
                    metadataHeaders=["From", "Subject"],
                ).execute()
                headers = msg.get("payload", {}).get("headers", [])
                emails.append({
                    "from": self._get_header(headers, "From"),
                    "subject": self._get_header(headers, "Subject") or "(No subject)",
                    "snippet": msg.get("snippet", "")[:100],
                })
            return emails

        except Exception as exc:
            logger.error("Error getting top emails: %s", exc)
            return []

    async def get_top_emails(self, count: int = 3) -> list[dict]:
        """Get top N emails as structured data."""
        return await self._run_sync(self._get_top_emails_sync, count)
