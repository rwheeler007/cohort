"""
Email receiver for incoming emails via Resend webhook.
Handles webhook validation, email parsing, and storage.
"""

import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import uuid

from models import (
    ReceivedEmail,
    EmailAttachment,
    ReceivedEmailStatus,
    Priority
)


class EmailReceiver:
    """Handles incoming email webhooks from Resend."""

    def __init__(self, data_dir: str = "data/comms_service", webhook_secret: Optional[str] = None):
        self.data_dir = Path(data_dir)
        self.received_dir = self.data_dir / "received_emails"
        self.webhook_secret = webhook_secret or os.getenv("RESEND_WEBHOOK_SECRET")

        # Create storage directories
        self._init_storage()

    def _init_storage(self):
        """Initialize storage directory structure."""
        for subdir in ["unprocessed", "routed", "responded", "archived", "spam", "error"]:
            (self.received_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _detect_project_from_recipients(self, recipients: List[str]) -> Optional[str]:
        """
        Detect project from recipient email addresses.

        Maps incoming email addresses to projects:
        - boss@partspec.ai -> partspec
        - support@partspec.ai -> partspec
        - contact@chillguard.com -> chillguard
        - etc.

        Args:
            recipients: List of recipient email addresses

        Returns:
            Project ID string or None if no match
        """
        if not recipients:
            return None

        # Project domain mapping
        domain_to_project = {
            "partspec.ai": "partspec",
            "chillguard.com": "chillguard",
            "chillguard.io": "chillguard",
        }

        # Check each recipient
        for email in recipients:
            if not email:
                continue

            # Extract domain from email
            if "@" in email:
                domain = email.split("@")[1].lower()

                # Check for exact domain match
                if domain in domain_to_project:
                    return domain_to_project[domain]

        # No match found
        return None

    def validate_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Validate Resend webhook signature.

        Args:
            payload: Raw webhook payload bytes
            signature: Signature from Resend-Signature header

        Returns:
            True if signature is valid
        """
        if not self.webhook_secret:
            print("[!] Warning: No webhook secret configured, skipping validation")
            return True  # Allow for development without secret

        try:
            # Resend uses HMAC-SHA256
            expected_signature = hmac.new(
                self.webhook_secret.encode(),
                payload,
                hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(signature, expected_signature)
        except Exception as e:
            print(f"[X] Webhook signature validation failed: {e}")
            return False

    def parse_resend_webhook(self, webhook_data: Dict) -> Optional[ReceivedEmail]:
        """
        Parse incoming email from Resend webhook payload.

        Resend webhook format:
        {
          "type": "email.received",
          "created_at": "2024-01-01T12:00:00Z",
          "data": {
            "from": "sender@example.com",
            "to": ["boss@partspec.ai"],
            "subject": "Test email",
            "text": "Email body text",
            "html": "<html>...</html>",
            "headers": {...},
            "attachments": [...]
          }
        }

        Args:
            webhook_data: Parsed webhook JSON

        Returns:
            ReceivedEmail object or None if parsing fails
        """
        try:
            # Check webhook type
            webhook_type = webhook_data.get("type")
            if webhook_type != "email.received":
                print(f"[!] Ignoring webhook type: {webhook_type}")
                return None

            data = webhook_data.get("data", {})

            # Generate email ID
            email_id = f"rcv_{uuid.uuid4().hex[:12]}"

            # Parse sender
            from_address = data.get("from")
            from_name = data.get("from_name")

            # Parse recipients
            to = data.get("to", [])
            if isinstance(to, str):
                to = [to]

            cc = data.get("cc", [])
            if cc and isinstance(cc, str):
                cc = [cc]

            # Parse subject and body
            subject = data.get("subject", "(No subject)")
            body_text = data.get("text", "")
            body_html = data.get("html")

            # Parse attachments
            attachments = []
            for att in data.get("attachments", []):
                attachments.append(EmailAttachment(
                    filename=att.get("filename", "attachment"),
                    content_type=att.get("content_type", "application/octet-stream"),
                    size_bytes=att.get("size", 0),
                    url=att.get("url"),
                    content=att.get("content")  # Base64 if inline
                ))

            # Parse headers
            headers = data.get("headers", {})

            # Extract threading info
            in_reply_to = headers.get("In-Reply-To")
            thread_id = headers.get("Thread-ID") or headers.get("References", "").split()[0] if headers.get("References") else None

            # Parse timestamp
            received_at = datetime.fromisoformat(
                webhook_data.get("created_at", datetime.utcnow().isoformat()).replace("Z", "+00:00")
            )

            # Detect project from recipient email address
            project_id = self._detect_project_from_recipients(to)

            # Create metadata with project tag
            metadata = {"project": project_id} if project_id else {}

            email = ReceivedEmail(
                email_id=email_id,
                from_address=from_address,
                from_name=from_name,
                to=to,
                cc=cc or None,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments,
                headers=headers,
                status=ReceivedEmailStatus.UNPROCESSED,
                in_reply_to=in_reply_to,
                thread_id=thread_id,
                received_at=received_at,
                metadata=metadata,
            )

            return email

        except Exception as e:
            print(f"[X] Failed to parse email webhook: {e}")
            import traceback
            traceback.print_exc()
            return None

    def store_email(self, email: ReceivedEmail) -> bool:
        """
        Store received email to filesystem.

        Args:
            email: ReceivedEmail object

        Returns:
            True if stored successfully
        """
        try:
            # Determine storage directory based on status
            status_dir = self.received_dir / email.status.value

            # Store as JSON
            filepath = status_dir / f"{email.email_id}.json"

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(email.model_dump(mode="json"), f, indent=2, default=str)

            print(f"[OK] Stored email {email.email_id} to {filepath}")
            return True

        except Exception as e:
            print(f"[X] Failed to store email {email.email_id}: {e}")
            return False

    def get_email(self, email_id: str) -> Optional[ReceivedEmail]:
        """
        Retrieve email by ID.

        Args:
            email_id: Email ID

        Returns:
            ReceivedEmail object or None
        """
        # Search across all status directories
        for status in ReceivedEmailStatus:
            filepath = self.received_dir / status.value / f"{email_id}.json"
            if filepath.exists():
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return ReceivedEmail(**data)
                except Exception as e:
                    print(f"[X] Failed to load email {email_id}: {e}")
                    return None

        return None

    def update_email(self, email: ReceivedEmail) -> bool:
        """
        Update email record and move between directories if status changed.

        Args:
            email: Updated email object

        Returns:
            True if updated successfully
        """
        try:
            # Find current location
            current_file = None
            for status in ReceivedEmailStatus:
                filepath = self.received_dir / status.value / f"{email.email_id}.json"
                if filepath.exists():
                    current_file = filepath
                    break

            if not current_file:
                print(f"[!] Email {email.email_id} not found, creating new")
                return self.store_email(email)

            # Determine new location
            new_dir = self.received_dir / email.status.value
            new_file = new_dir / f"{email.email_id}.json"

            # Write to new location
            with open(new_file, "w", encoding="utf-8") as f:
                json.dump(email.model_dump(mode="json"), f, indent=2, default=str)

            # Remove old file if different location
            if current_file != new_file:
                current_file.unlink()
                print(f"[OK] Moved email {email.email_id}: {current_file.parent.name} -> {email.status.value}")

            return True

        except Exception as e:
            print(f"[X] Failed to update email {email.email_id}: {e}")
            return False

    def list_emails(
        self,
        status: Optional[ReceivedEmailStatus] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[ReceivedEmail]:
        """
        List received emails with optional filtering.

        Args:
            status: Filter by status (None = all)
            limit: Max results
            offset: Skip first N results

        Returns:
            List of ReceivedEmail objects
        """
        emails = []

        # Determine which directories to search
        if status:
            search_dirs = [self.received_dir / status.value]
        else:
            search_dirs = [self.received_dir / s.value for s in ReceivedEmailStatus]

        # Collect all email files
        email_files = []
        for dir_path in search_dirs:
            if dir_path.exists():
                email_files.extend(dir_path.glob("*.json"))

        # Sort by modification time (newest first)
        email_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        # Apply pagination
        for filepath in email_files[offset:offset + limit]:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                emails.append(ReceivedEmail(**data))
            except Exception as e:
                print(f"[X] Failed to load {filepath}: {e}")

        return emails

    def get_stats(self) -> Dict[str, int]:
        """
        Get email statistics by status.

        Returns:
            Dict with counts for each status
        """
        stats = {}

        for status in ReceivedEmailStatus:
            dir_path = self.received_dir / status.value
            if dir_path.exists():
                count = len(list(dir_path.glob("*.json")))
                stats[status.value] = count
            else:
                stats[status.value] = 0

        # Add received_today count
        today = datetime.utcnow().date()
        received_today = 0

        for status in ReceivedEmailStatus:
            dir_path = self.received_dir / status.value
            if dir_path.exists():
                for filepath in dir_path.glob("*.json"):
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        received_at = datetime.fromisoformat(data["received_at"].replace("Z", "+00:00"))
                        if received_at.date() == today:
                            received_today += 1
                    except:
                        pass

        stats["received_today"] = received_today

        return stats

    def receive_webhook(self, webhook_data: Dict, signature: Optional[str] = None) -> Optional[ReceivedEmail]:
        """
        Main entry point for receiving email webhooks.

        Args:
            webhook_data: Parsed webhook JSON
            signature: Webhook signature for validation (optional)

        Returns:
            ReceivedEmail object if successful, None otherwise
        """
        # Validate signature if provided
        if signature:
            payload_bytes = json.dumps(webhook_data).encode()
            if not self.validate_webhook_signature(payload_bytes, signature):
                print("[X] Invalid webhook signature")
                return None

        # Parse email
        email = self.parse_resend_webhook(webhook_data)
        if not email:
            return None

        # Store email
        if not self.store_email(email):
            return None

        print(f"[OK] Received email {email.email_id} from {email.from_address}")
        return email
