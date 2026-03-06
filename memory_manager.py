"""
memory_manager.py - Persistent conversation history and user preferences.

Stores data as JSON files in the configured DATA_DIR so the agent
remembers context across restarts.
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("openpaw.memory")

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


class MemoryManager:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._history_file = self.data_dir / "conversation_history.json"
        self._prefs_file = self.data_dir / "preferences.json"
        self._reminders_file = self.data_dir / "reminders.json"

        self._history: dict[str, list[dict]] = {}   # {user_id_str: [messages]}
        self._preferences: dict[str, dict] = {}
        self._reminders: list[dict] = []

        self._load_all()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _load_json(self, path: Path, default):
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load %s: %s", path, exc)
        return default

    def _save_json(self, path: Path, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error("Failed to save %s: %s", path, exc)

    def _load_all(self):
        self._history = self._load_json(self._history_file, {})
        self._preferences = self._load_json(self._prefs_file, {})
        self._reminders = self._load_json(self._reminders_file, [])
        logger.info("Loaded memory from %s", self.data_dir)

    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------
    def get_history(self, user_id: int) -> list[dict]:
        """Return the conversation history for *user_id*."""
        return self._history.get(str(user_id), [])

    def add_message(self, user_id: int, role: str, content: str):
        """Append a message to the conversation history."""
        key = str(user_id)
        if key not in self._history:
            self._history[key] = []
        self._history[key].append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })
        # Keep history to a reasonable size (last 100 messages)
        if len(self._history[key]) > 100:
            self._history[key] = self._history[key][-100:]
        self._save_json(self._history_file, self._history)

    def clear_history(self, user_id: int):
        """Clear conversation history for *user_id*."""
        key = str(user_id)
        self._history.pop(key, None)
        self._save_json(self._history_file, self._history)
        logger.info("Cleared history for user %s", user_id)

    def get_ollama_messages(self, user_id: int) -> list[dict]:
        """Return history formatted for Ollama's /api/chat endpoint.

        Strips timestamps and returns only role + content.
        """
        raw = self.get_history(user_id)
        return [{"role": m["role"], "content": m["content"]} for m in raw]

    def get_all_history(self) -> dict[str, list[dict]]:
        """Return the full raw history dict for backfilling into embedding store."""
        return self._history

    # ------------------------------------------------------------------
    # User preferences
    # ------------------------------------------------------------------
    def get_pref(self, user_id: int, key: str, default=None):
        return self._preferences.get(str(user_id), {}).get(key, default)

    def set_pref(self, user_id: int, key: str, value):
        uid = str(user_id)
        if uid not in self._preferences:
            self._preferences[uid] = {}
        self._preferences[uid][key] = value
        self._save_json(self._prefs_file, self._preferences)

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------
    def add_reminder(self, user_id: int, text: str, trigger_time: float):
        self._reminders.append({
            "user_id": user_id,
            "text": text,
            "trigger_time": trigger_time,
            "fired": False,
        })
        self._save_json(self._reminders_file, self._reminders)

    def get_due_reminders(self) -> list[dict]:
        """Return reminders that are due and not yet fired."""
        now = time.time()
        due = [r for r in self._reminders if not r["fired"] and r["trigger_time"] <= now]
        return due

    def mark_reminder_fired(self, reminder: dict):
        reminder["fired"] = True
        self._save_json(self._reminders_file, self._reminders)

    def get_reminders(self, user_id: int) -> list[dict]:
        """Return all active (unfired) reminders for a user."""
        return [
            r for r in self._reminders
            if r["user_id"] == user_id and not r["fired"]
        ]
