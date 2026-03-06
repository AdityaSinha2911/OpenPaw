"""
user_profile.py - Persistent personal profile that learns from user messages.

Stores preferences, aliases, custom rules, interests, routines and more.
The profile is injected into the system prompt so the LLM always has user context.
"""

import json
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger("openpaw.profile")

_DEFAULT_PROFILE = {
    "name": "",
    "preferences": {},
    "aliases": {},
    "frequent_apps": [],
    "frequent_paths": [],
    "frequent_contacts": [],
    "custom_rules": [],
    "interests": [],
    "routines": {},
    "last_updated": "",
}

# Patterns that indicate the user is explicitly telling us something to remember
_REMEMBER_PATTERNS = [
    re.compile(r"remember\s+that\s+(.+)", re.IGNORECASE),
    re.compile(r"my\s+(\w[\w\s]*?)\s+is\s+(.+)", re.IGNORECASE),
    re.compile(r"always\s+(.+?)(?:\s+when\s+(.+))?$", re.IGNORECASE),
    re.compile(r"call\s+me\s+(\w+)", re.IGNORECASE),
    re.compile(r"i(?:'m|\s+am)\s+(\w+)", re.IGNORECASE),
]

# Words that hint toward common preference categories
_APP_KEYWORDS = {
    "vscode", "vs code", "chrome", "firefox", "edge", "notepad",
    "sublime", "telegram", "discord", "slack", "spotify", "vlc",
    "word", "excel", "powerpoint", "outlook", "obs", "git",
}

_INTEREST_KEYWORDS = {
    "ai", "programming", "python", "javascript", "music", "gaming",
    "machine learning", "web dev", "linux", "crypto", "anime",
    "photography", "design", "cooking", "fitness", "reading",
}


class UserProfile:
    """Persistent user profile that learns from messages over time."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._profile_path = os.path.join(data_dir, "user_profile.json")
        self._message_count = 0
        self._profile: dict = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self):
        if os.path.exists(self._profile_path):
            try:
                with open(self._profile_path, "r", encoding="utf-8") as f:
                    self._profile = json.load(f)
                logger.info("Loaded user profile from %s", self._profile_path)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load profile: %s", exc)
                self._profile = dict(_DEFAULT_PROFILE)
        else:
            self._profile = dict(_DEFAULT_PROFILE)

    def _save(self):
        self._profile["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(self._profile_path, "w", encoding="utf-8") as f:
                json.dump(self._profile, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error("Failed to save profile: %s", exc)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------
    def get_profile(self) -> dict:
        return dict(self._profile)

    def get_aliases(self) -> dict:
        return dict(self._profile.get("aliases", {}))

    # ------------------------------------------------------------------
    # Explicit actions (from action tags)
    # ------------------------------------------------------------------
    def set_preference(self, key: str, value: str) -> str:
        self._profile.setdefault("preferences", {})[key] = value
        self._save()
        logger.info("Profile preference set: %s = %s", key, value)
        return f"Preference saved: {key} = {value}"

    def get_preference(self, key: str) -> str:
        value = self._profile.get("preferences", {}).get(key)
        if value is None:
            # Check top-level keys too
            value = self._profile.get(key)
        if value is None:
            return f"No preference found for '{key}'."
        return f"{key}: {value}"

    def add_alias(self, alias: str, full_value: str) -> str:
        self._profile.setdefault("aliases", {})[alias.lower()] = full_value
        self._save()
        logger.info("Alias added: '%s' -> '%s'", alias, full_value)
        return f"Alias saved: '{alias}' = '{full_value}'"

    def add_rule(self, rule: str) -> str:
        rules = self._profile.setdefault("custom_rules", [])
        if rule not in rules:
            rules.append(rule)
            self._save()
            logger.info("Custom rule added: %s", rule)
        return f"Rule saved: {rule}"

    def add_contact(self, name: str) -> str:
        contacts = self._profile.setdefault("frequent_contacts", [])
        if name not in contacts:
            contacts.append(name)
            self._save()
        return f"Contact added: {name}"

    def show_profile(self) -> str:
        """Return a human-readable summary of the profile."""
        p = self._profile
        lines = ["=== User Profile ==="]
        if p.get("name"):
            lines.append(f"Name: {p['name']}")
        if p.get("preferences"):
            prefs = ", ".join(f"{k}: {v}" for k, v in p["preferences"].items())
            lines.append(f"Preferences: {prefs}")
        if p.get("aliases"):
            aliases = ", ".join(f"'{k}' = '{v}'" for k, v in p["aliases"].items())
            lines.append(f"Aliases: {aliases}")
        if p.get("custom_rules"):
            lines.append("Custom rules:")
            for r in p["custom_rules"]:
                lines.append(f"  - {r}")
        if p.get("interests"):
            lines.append(f"Interests: {', '.join(p['interests'])}")
        if p.get("frequent_apps"):
            lines.append(f"Frequent apps: {', '.join(p['frequent_apps'])}")
        if p.get("frequent_paths"):
            lines.append(f"Frequent paths: {', '.join(p['frequent_paths'])}")
        if p.get("frequent_contacts"):
            lines.append(f"Frequent contacts: {', '.join(p['frequent_contacts'])}")
        if p.get("routines"):
            lines.append(f"Routines: {json.dumps(p['routines'], indent=2)}")
        if p.get("last_updated"):
            lines.append(f"Last updated: {p['last_updated']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # System prompt injection
    # ------------------------------------------------------------------
    def get_system_prompt_section(self) -> str:
        """Return a block of text to inject into the LLM system prompt."""
        p = self._profile
        # Only produce output if there is something to say
        parts = []

        if p.get("name"):
            parts.append(f"Name: {p['name']}")

        if p.get("preferences"):
            prefs = ", ".join(f"{k}: {v}" for k, v in p["preferences"].items())
            parts.append(f"Preferences: {prefs}")

        if p.get("aliases"):
            aliases = ", ".join(f"'{k}' = '{v}'" for k, v in p["aliases"].items())
            parts.append(f"Aliases: {aliases}")

        if p.get("custom_rules"):
            parts.append(f"Custom rules: {'; '.join(p['custom_rules'])}")

        if p.get("interests"):
            parts.append(f"Interests: {', '.join(p['interests'])}")

        if p.get("frequent_apps"):
            parts.append(f"Frequent apps: {', '.join(p['frequent_apps'])}")

        if p.get("frequent_contacts"):
            parts.append(f"Frequent contacts: {', '.join(p['frequent_contacts'])}")

        if not parts:
            return ""

        return "ABOUT THE USER:\n" + "\n".join(parts)

    # ------------------------------------------------------------------
    # Alias resolution
    # ------------------------------------------------------------------
    def resolve_aliases(self, text: str) -> str:
        """Replace all known aliases in *text* with their full values."""
        aliases = self._profile.get("aliases", {})
        if not aliases:
            return text
        result = text
        # Sort by length descending so longer aliases match first
        for alias in sorted(aliases, key=len, reverse=True):
            # Case-insensitive replacement
            pattern = re.compile(re.escape(alias), re.IGNORECASE)
            result = pattern.sub(aliases[alias], result)
        return result

    # ------------------------------------------------------------------
    # Passive learning
    # ------------------------------------------------------------------
    def learn_from_message(self, message: str):
        """Analyze a user message and extract any useful profile information.

        Called silently after every user message.
        """
        self._message_count += 1
        lower = message.lower()
        changed = False

        # Detect explicit "call me X" / "my name is X"
        m = re.search(r"call\s+me\s+(\w+)", lower)
        if m:
            self._profile["name"] = m.group(1).title()
            changed = True

        m = re.search(r"my\s+name\s+is\s+(\w+)", lower)
        if m:
            self._profile["name"] = m.group(1).title()
            changed = True

        # Detect "remember that ..." — store as preference
        m = re.search(r"remember\s+that\s+(.+)", lower)
        if m:
            fact = m.group(1).strip().rstrip(".")
            self._profile.setdefault("preferences", {})["remembered"] = fact
            changed = True

        # Detect "my X is Y" patterns
        m = re.search(r"my\s+(\w[\w\s]{0,20}?)\s+is\s+(.+?)(?:\.|$)", lower)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip()
            if key not in ("name",):  # name handled above
                self._profile.setdefault("preferences", {})[key] = value
                changed = True

        # Detect "always do X when Y" — store as rule
        m = re.search(r"always\s+(.+?)(?:\s+when\s+(.+))?$", lower)
        if m:
            rule = m.group(0).strip()
            rules = self._profile.setdefault("custom_rules", [])
            if rule not in rules:
                rules.append(rule)
                changed = True

        # Detect "never X" — store as rule
        m = re.search(r"never\s+(.+?)(?:\.|$)", lower)
        if m:
            rule = m.group(0).strip().rstrip(".")
            rules = self._profile.setdefault("custom_rules", [])
            if rule not in rules:
                rules.append(rule)
                changed = True

        # Detect app mentions
        apps = self._profile.setdefault("frequent_apps", [])
        for app in _APP_KEYWORDS:
            if app in lower and app not in apps:
                apps.append(app)
                changed = True

        # Detect interest mentions
        interests = self._profile.setdefault("interests", [])
        for interest in _INTEREST_KEYWORDS:
            if interest in lower and interest not in interests:
                interests.append(interest)
                changed = True

        # Detect path mentions (Windows paths)
        path_matches = re.findall(r"[A-Za-z]:\\[\w\\.\-\s]+", message)
        paths = self._profile.setdefault("frequent_paths", [])
        for p in path_matches:
            normalized = p.strip().rstrip("\\")
            if normalized not in paths and len(paths) < 20:
                paths.append(normalized)
                changed = True

        if changed:
            self._save()
