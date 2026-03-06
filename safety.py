"""
safety.py - Path blacklist, command blacklist, and confirmation system.

This is the critical safety layer that protects the system from destructive
operations. Every file operation and shell command passes through here.
"""

import os
import re
import string
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("openpaw.safety")

# ---------------------------------------------------------------------------
# Path blacklist – absolute paths the agent must NEVER touch
# ---------------------------------------------------------------------------
BLACKLISTED_PATHS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\Windows\System32",
    r"C:\Windows\SysWOW64",
    r"C:\System32",
]

# Keywords are matched against the forward-slash normalised path produced by
# normalize_path(), so only forward-slash forms are needed here.
BLACKLISTED_PATH_KEYWORDS = [
    "system32", "syswow64", "winsxs", "boot", "recovery",
    "bios", "/efi/", "registry",
    "/windows/", "/system volume information",
]

# ---------------------------------------------------------------------------
# Command blacklist – shell tokens that are never allowed
# ---------------------------------------------------------------------------
BLOCKED_COMMAND_PATTERNS = [
    r"\bformat\b",
    r"\brmdir\s+/s\b",
    r"\bdel\s+/f\s+/s\s+/q\b",
    r"\brm\s+-rf\b",
    r"\bshutdown\b",
    r"\brestart\b",
    r"\breg\s+delete\b",
    r"\breg\s+add\b",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\bcipher\s+/w\b",
    r"\bsfc\b",
    r"\bdism\b",
    r"\bnet\s+user\b",
    r"\bnet\s+stop\b",
    r"\bnet\s+start\b",
    r"\btakeown\b",
    r"\bicacls\b",
    r"\bcmd\.exe\s*/c\s.*del\b",
    r"\btaskkill\b.*/f.*/im\s+(svchost|lsass|csrss|winlogon|explorer|smss|wininit)",
]

# ---------------------------------------------------------------------------
# Protected system processes – must never be killed
# ---------------------------------------------------------------------------
PROTECTED_PROCESSES: set[str] = {
    "svchost.exe", "lsass.exe", "csrss.exe", "winlogon.exe",
    "explorer.exe", "system", "registry", "smss.exe", "wininit.exe",
}


def is_process_protected(name: str) -> bool:
    """Return True if *name* is a critical Windows system process."""
    return name.lower().strip() in {p.lower() for p in PROTECTED_PROCESSES}


# ---------------------------------------------------------------------------
# Sensitive file patterns – must never be read or accessed
# ---------------------------------------------------------------------------
_SENSITIVE_BASENAMES: set[str] = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "wallet.dat", "private_key", "secret_key",
    "login data",      # Chrome password store
    "logins.json",     # Firefox password store
    "key3.db", "key4.db", "cert9.db",
}

_SENSITIVE_EXTENSIONS: set[str] = {
    ".pem", ".p12", ".pfx", ".key", ".jks", ".keystore",
}


def is_sensitive_file(path: str) -> bool:
    """Return True if the file at *path* looks like a sensitive/private file."""
    basename = os.path.basename(path).lower()
    _, ext = os.path.splitext(basename)
    if basename in _SENSITIVE_BASENAMES:
        return True
    if ext in _SENSITIVE_EXTENSIONS:
        return True
    # Catch .env.* variants
    if basename.startswith(".env"):
        return True
    # Catch private-key files regardless of extension
    if "private" in basename and "key" in basename:
        return True
    return False


# ---------------------------------------------------------------------------
# Dynamic Windows folder resolution
# ---------------------------------------------------------------------------

def get_windows_folder(name: str) -> str:
    """Return the absolute path for a named standard Windows user folder.

    Paths are always resolved at runtime — never hardcoded.
    """
    home = os.path.expanduser("~")
    mapping: dict[str, str] = {
        "desktop":       os.path.join(home, "Desktop"),
        "downloads":     os.path.join(home, "Downloads"),
        "documents":     os.path.join(home, "Documents"),
        "pictures":      os.path.join(home, "Pictures"),
        "videos":        os.path.join(home, "Videos"),
        "music":         os.path.join(home, "Music"),
        "appdata":       os.path.expandvars("%APPDATA%"),
        "localappdata":  os.path.expandvars("%LOCALAPPDATA%"),
        "programfiles":  os.path.expandvars("%PROGRAMFILES%"),
        "programfilesx86": os.path.expandvars("%PROGRAMFILES(X86)%"),
        "temp":          os.path.expandvars("%TEMP%"),
        "windir":        os.path.expandvars("%WINDIR%"),
    }
    key = name.lower().replace(" ", "").replace("_", "")
    return mapping.get(key, "")


def get_all_drives() -> list[str]:
    """Return root paths of every drive currently present on this system."""
    return [
        f"{letter}:\\"
        for letter in string.ascii_uppercase
        if os.path.exists(f"{letter}:\\")
    ]


# ---------------------------------------------------------------------------
# Actions that require confirmation before execution
# ---------------------------------------------------------------------------
DESTRUCTIVE_ACTIONS = {
    "delete_file", "delete_folder",
    "move_file", "move_folder",
    "rename_file", "rename_folder",
    "run_command",
    "kill_process",
    "write_file",          # overwriting existing file
    "modify_system",
}


def normalize_path(p: str) -> str:
    """Resolve and normalize a path for comparison."""
    return str(Path(p).resolve()).replace("\\", "/").lower()


def is_path_blacklisted(target: str) -> bool:
    """Return True if *target* falls under a blacklisted directory."""
    norm = normalize_path(target)
    for bp in BLACKLISTED_PATHS:
        bp_norm = normalize_path(bp)
        if norm == bp_norm or norm.startswith(bp_norm + "/"):
            logger.warning("Blocked operation on blacklisted path: %s", target)
            return True
    for kw in BLACKLISTED_PATH_KEYWORDS:
        if kw.lower() in norm:
            logger.warning("Blocked operation – path contains keyword '%s': %s", kw, target)
            return True
    return False


def is_path_in_allowed_dirs(target: str, allowed_dirs: list[str]) -> bool:
    """Return True if *target* is inside one of the allowed working dirs."""
    norm = normalize_path(target)
    for ad in allowed_dirs:
        ad_norm = normalize_path(ad)
        if norm == ad_norm or norm.startswith(ad_norm + "/"):
            return True
    return False


def is_command_blocked(command: str) -> bool:
    """Return True if *command* matches a blocked pattern."""
    cmd_lower = command.lower()
    for pat in BLOCKED_COMMAND_PATTERNS:
        if re.search(pat, cmd_lower):
            logger.warning("Blocked dangerous command: %s", command)
            return True
    return False


# ---------------------------------------------------------------------------
# Confirmation manager – tracks pending confirmations per user
# ---------------------------------------------------------------------------
class ConfirmationManager:
    """Manages pending confirmations for destructive actions.

    Flow:
      1. Agent calls request_confirmation(user_id, description, callback).
      2. Bot sends the description + "Confirm? yes/no" to the user.
      3. If the user replies "yes" within the timeout, the callback fires.
      4. If "no" or timeout, the action is cancelled.
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        # {user_id: {"description": str, "callback": coroutine, "task": asyncio.Task}}
        self._pending: dict[int, dict] = {}

    def has_pending(self, user_id: int) -> bool:
        return user_id in self._pending

    async def request_confirmation(
        self,
        user_id: int,
        description: str,
        callback,       # async callable – executed on "yes"
        send_fn,        # async callable(user_id, text) to message the user
    ) -> None:
        # Cancel any existing pending confirmation for this user
        await self.cancel(user_id, notify=False)

        async def _timeout_task():
            await asyncio.sleep(self.timeout)
            if user_id in self._pending:
                del self._pending[user_id]
                await send_fn(user_id, "Confirmation timed out. Action cancelled.")
                logger.info("Confirmation timed out for user %s", user_id)

        loop = asyncio.get_event_loop()
        task = loop.create_task(_timeout_task())

        self._pending[user_id] = {
            "description": description,
            "callback": callback,
            "task": task,
        }

        await send_fn(
            user_id,
            f"⚠ Confirmation required:\n\n{description}\n\nReply *yes* to proceed or *no* to cancel.",
        )
        logger.info("Confirmation requested for user %s: %s", user_id, description)

    async def handle_response(self, user_id: int, response: str) -> str | None:
        """Process a yes/no reply. Returns a status message or None."""
        if user_id not in self._pending:
            return None

        entry = self._pending.pop(user_id)
        entry["task"].cancel()

        answer = response.strip().lower()
        if answer in ("yes", "y"):
            logger.info("User %s confirmed: %s", user_id, entry["description"])
            try:
                result = await entry["callback"]()
                return result if isinstance(result, str) else "Done."
            except Exception as exc:
                logger.exception("Error executing confirmed action")
                return f"Error executing action: {exc}"
        else:
            logger.info("User %s cancelled: %s", user_id, entry["description"])
            return "Action cancelled."

    async def cancel(self, user_id: int, notify: bool = True) -> None:
        if user_id in self._pending:
            entry = self._pending.pop(user_id)
            entry["task"].cancel()
            if notify:
                logger.info("Cancelled pending confirmation for user %s", user_id)
