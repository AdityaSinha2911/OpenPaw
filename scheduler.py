"""
scheduler.py - Proactive heartbeat: battery alerts, reminders, folder watching,
calendar alerts, email digests, and morning briefing.

Runs background tasks on a configurable interval using asyncio.
"""

import asyncio
import logging
import os
import time
from datetime import datetime
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
        gmail=None,
        calendar=None,
    ):
        self.memory = memory_manager
        self.send_fn = send_fn
        self.owner_id = owner_id
        self.heartbeat_interval = heartbeat_interval * 60  # seconds
        self.battery_threshold = battery_threshold
        self.watch_folders = watch_folders or []
        self.gmail = gmail
        self.calendar = calendar

        self._running = False
        self._task: asyncio.Task | None = None
        self._last_battery_alert: float = 0
        self._folder_snapshots: dict[str, set[str]] = {}

        # Tracking for calendar and email proactive checks
        self._alerted_event_ids: set[str] = set()
        self._last_email_check: float = 0
        self._last_morning_briefing: str = ""  # date string YYYY-MM-DD
        self._tick_count: int = 0

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
                self._tick_count += 1
                await self._check_battery()
                await self._check_reminders()
                await self._check_folders()
                await self._check_calendar_alerts()
                await self._check_unread_emails()
                await self._check_morning_briefing()
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

    # ------------------------------------------------------------------
    # Calendar alerts (every heartbeat — check events in next 30 min)
    # ------------------------------------------------------------------
    async def _check_calendar_alerts(self):
        if not self.calendar:
            return
        if not self.calendar.is_authenticated():
            return

        try:
            upcoming = await self.calendar.get_upcoming_events(minutes_ahead=35)
            for event in upcoming:
                event_id = event.get("id", "")
                if event_id in self._alerted_event_ids:
                    continue

                title = event.get("summary", "(No title)")
                start = event.get("start", {})
                start_time = start.get("dateTime", start.get("date", ""))
                location = event.get("location", "")

                try:
                    start_dt = datetime.fromisoformat(start_time)
                    time_str = start_dt.strftime("%H:%M")
                    minutes_until = int((start_dt.timestamp() - time.time()) / 60)
                    if minutes_until < 0:
                        minutes_until = 0
                except (ValueError, TypeError):
                    time_str = start_time
                    minutes_until = "?"

                msg = (
                    f"Upcoming event in ~{minutes_until} minutes:\n"
                    f"  {title} at {time_str}"
                )
                if location:
                    msg += f"\n  Location: {location}"

                await self.send_fn(self.owner_id, msg)
                self._alerted_event_ids.add(event_id)
                logger.info("Calendar alert sent: %s", title)

            # Clean up old event IDs (keep set from growing unbounded)
            if len(self._alerted_event_ids) > 100:
                self._alerted_event_ids = set(list(self._alerted_event_ids)[-50:])

        except Exception as exc:
            logger.error("Error checking calendar alerts: %s", exc)

    # ------------------------------------------------------------------
    # Unread email check (every ~1 hour — every 12 ticks at 5 min interval)
    # ------------------------------------------------------------------
    async def _check_unread_emails(self):
        if not self.gmail:
            return
        if not self.gmail.is_authenticated():
            return

        # Only check roughly every hour (12 * 5 min = 60 min)
        if self._tick_count % 12 != 0:
            return

        try:
            unread_count = await self.gmail.get_unread_count()
            if unread_count >= 5:
                await self.send_fn(
                    self.owner_id,
                    f"You have {unread_count} unread emails. "
                    f"Say 'read my unread emails' or 'summarize my emails' to catch up.",
                )
                logger.info("Sent unread email alert: %d unread", unread_count)
        except Exception as exc:
            logger.error("Error checking unread emails: %s", exc)

    # ------------------------------------------------------------------
    # Morning briefing (8 AM daily)
    # ------------------------------------------------------------------
    async def _check_morning_briefing(self):
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # Already sent today's briefing
        if self._last_morning_briefing == today_str:
            return

        # Only send between 8:00 and 8:10 (to allow for heartbeat interval)
        if now.hour != 8 or now.minute > 10:
            return

        self._last_morning_briefing = today_str

        lines = [f"Good morning! Here's your briefing for {today_str}:"]

        # Calendar events count
        if self.calendar and self.calendar.is_authenticated():
            try:
                event_count = await self.calendar.get_today_count()
                lines.append(f"  Calendar: {event_count} event(s) today")
            except Exception:
                lines.append("  Calendar: could not fetch events")

        # Unread email count + top 3
        if self.gmail and self.gmail.is_authenticated():
            try:
                unread_count = await self.gmail.get_unread_count()
                lines.append(f"  Email: {unread_count} unread email(s)")

                top_emails = await self.gmail.get_top_emails(3)
                if top_emails:
                    lines.append("  Top emails:")
                    for i, email in enumerate(top_emails):
                        lines.append(
                            f"    {i + 1}. {email['from']}: {email['subject']}"
                        )
            except Exception:
                lines.append("  Email: could not fetch emails")

        await self.send_fn(self.owner_id, "\n".join(lines))
        logger.info("Sent morning briefing")
