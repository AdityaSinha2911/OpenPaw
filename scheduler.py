"""
scheduler.py - Proactive heartbeat: battery alerts, reminders, folder watching.

Runs background tasks on a configurable interval using asyncio.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from system_tools import get_battery_percent, is_on_battery

logger = logging.getLogger("openpaw.scheduler")


class ProactiveScheduler:
    """Background scheduler that fires proactive events."""

    def __init__(
        self,
        memory_manager,
        send_fn,                    # async callable(user_id, text)
        owner_id: int,
        heartbeat_interval: int = 5,   # minutes
        battery_threshold: int = 20,
        watch_folders: list[str] | None = None,
    ):
        self.memory = memory_manager
        self.send_fn = send_fn
        self.owner_id = owner_id
        self.heartbeat_interval = heartbeat_interval * 60  # seconds
        self.battery_threshold = battery_threshold
        self.watch_folders = watch_folders or []

        self._running = False
        self._task: asyncio.Task | None = None
        self._last_battery_alert: float = 0
        self._folder_snapshots: dict[str, set[str]] = {}

        # Initialize folder snapshots
        for folder in self.watch_folders:
            self._folder_snapshots[folder] = self._snapshot_folder(folder)

    @staticmethod
    def _snapshot_folder(folder: str) -> set[str]:
        """Return a set of filenames in *folder*."""
        try:
            p = Path(folder)
            if p.is_dir():
                return {e.name for e in p.iterdir()}
        except OSError:
            pass
        return set()

    async def start(self):
        """Start the background scheduler loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.get_event_loop().create_task(self._loop())
        logger.info("Scheduler started (interval=%ds)", self.heartbeat_interval)

    async def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    async def _loop(self):
        """Main heartbeat loop."""
        while self._running:
            try:
                await self._check_battery()
                await self._check_reminders()
                await self._check_folders()
            except Exception:
                logger.exception("Error in scheduler heartbeat")
            await asyncio.sleep(self.heartbeat_interval)

    # ------------------------------------------------------------------
    # Battery monitoring
    # ------------------------------------------------------------------
    async def _check_battery(self):
        pct = get_battery_percent()
        if pct is None:
            return
        if pct <= self.battery_threshold and is_on_battery():
            now = time.time()
            # Don't spam — alert at most once every 10 minutes
            if now - self._last_battery_alert > 600:
                self._last_battery_alert = now
                await self.send_fn(
                    self.owner_id,
                    f"Low battery alert: {pct}% remaining. Plug in soon!",
                )
                logger.info("Sent low battery alert: %d%%", pct)

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------
    async def _check_reminders(self):
        due = self.memory.get_due_reminders()
        for reminder in due:
            user_id = reminder["user_id"]
            text = reminder["text"]
            await self.send_fn(user_id, f"Reminder: {text}")
            self.memory.mark_reminder_fired(reminder)
            logger.info("Fired reminder for user %s: %s", user_id, text)

    # ------------------------------------------------------------------
    # Folder watching
    # ------------------------------------------------------------------
    async def _check_folders(self):
        for folder in self.watch_folders:
            current = self._snapshot_folder(folder)
            previous = self._folder_snapshots.get(folder, set())
            new_files = current - previous
            if new_files:
                names = ", ".join(sorted(new_files)[:10])
                extra = f" (and {len(new_files)-10} more)" if len(new_files) > 10 else ""
                await self.send_fn(
                    self.owner_id,
                    f"New files detected in {folder}:\n{names}{extra}",
                )
                logger.info("New files in %s: %s", folder, new_files)
            self._folder_snapshots[folder] = current
