"""
calendar_tools.py - Google Calendar API integration for OpenPaw.

Provides full calendar control: view, create, update, delete events,
set reminders, and natural language event creation via Ollama.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from functools import partial

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("openpaw.calendar")

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def _get_local_timezone() -> str:
    """Get the local timezone as an IANA string (e.g., 'Asia/Kolkata')."""
    try:
        from tzlocal import get_localzone
        tz = get_localzone()
        return str(tz)
    except ImportError:
        logger.warning("tzlocal not installed — falling back to UTC")
        return "UTC"
    except Exception:
        return "UTC"


class CalendarTools:
    """Google Calendar API wrapper with async-compatible methods."""

    def __init__(self, data_dir: str, ollama_connector=None):
        self.data_dir = data_dir
        self.ollama = ollama_connector
        self._token_path = os.path.join(data_dir, "calendar_token.json")
        self._service = None
        self.timezone = _get_local_timezone()

    # Authentication
    def _get_service(self):
        """Build or return a cached Calendar API service."""
        if self._service is not None:
            return self._service

        if not os.path.exists(self._token_path):
            raise FileNotFoundError(
                "Calendar token not found. Please run: python auth_google.py"
            )

        creds = Credentials.from_authorized_user_file(self._token_path, CALENDAR_SCOPES)

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(self._token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                logger.info("Calendar token refreshed successfully")
            except Exception as exc:
                logger.error("Failed to refresh Calendar token: %s", exc)
                raise RuntimeError(
                    "Calendar token expired and could not be refreshed. "
                    "Please run: python auth_google.py"
                ) from exc

        if not creds.valid:
            raise RuntimeError(
                "Calendar credentials are invalid. Please run: python auth_google.py"
            )

        self._service = build("calendar", "v3", credentials=creds)
        logger.info("Calendar API service initialized (timezone: %s)", self.timezone)
        return self._service

    def is_authenticated(self) -> bool:
        """Check if Calendar is authenticated and tokens are valid."""
        try:
            self._get_service()
            return True
        except Exception:
            return False

    # Internal helpers
    def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous function in the default executor."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, partial(func, *args, **kwargs))

    @staticmethod
    def _format_event(event: dict) -> str:
        """Format a calendar event into a readable string."""
        title = event.get("summary", "(No title)")
        event_id = event.get("id", "")
        location = event.get("location", "")
        description = event.get("description", "")
        attendees = event.get("attendees", [])

        start = event.get("start", {})
        end = event.get("end", {})

        # Handle all-day events vs timed events
        if "date" in start:
            start_str = start["date"]
            end_str = end.get("date", "")
            time_str = f"{start_str} (all day)"
        else:
            start_dt = start.get("dateTime", "")
            end_dt = end.get("dateTime", "")
            try:
                s = datetime.fromisoformat(start_dt)
                e = datetime.fromisoformat(end_dt)
                time_str = f"{s.strftime('%Y-%m-%d %H:%M')} - {e.strftime('%H:%M')}"
            except (ValueError, TypeError):
                time_str = f"{start_dt} - {end_dt}"

        lines = [f"  Title: {title}", f"  Time: {time_str}"]
        if location:
            lines.append(f"  Location: {location}")
        if description:
            desc_preview = description[:200]
            lines.append(f"  Description: {desc_preview}")
        if attendees:
            names = [a.get("email", "") for a in attendees[:5]]
            lines.append(f"  Attendees: {', '.join(names)}")
        lines.append(f"  ID: {event_id}")

        return "\n".join(lines)

    # View events
    def _get_events_sync(self, time_min: str, time_max: str) -> str:
        """Get events in a time range (synchronous)."""
        try:
            service = self._get_service()
            events_result = service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                timeZone=self.timezone,
            ).execute()

            events = events_result.get("items", [])
            if not events:
                return "No events found for this period."

            output = []
            for i, event in enumerate(events):
                output.append(f"{i + 1}.\n{self._format_event(event)}")

            return "\n\n".join(output)

        except HttpError as exc:
            logger.error("Calendar API error: %s", exc)
            return f"Calendar API error: {exc}"
        except Exception as exc:
            logger.error("Error fetching events: %s", exc)
            return f"Error fetching events: {exc}"

    async def get_today_events(self) -> str:
        """Get today's events."""
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        time_min = start.isoformat() + self._tz_offset()
        time_max = end.isoformat() + self._tz_offset()
        result = await self._run_sync(self._get_events_sync, time_min, time_max)
        return f"Today's events ({now.strftime('%Y-%m-%d')}):\n\n{result}"

    async def get_week_events(self) -> str:
        """Get this week's events (next 7 days)."""
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        time_min = start.isoformat() + self._tz_offset()
        time_max = end.isoformat() + self._tz_offset()
        result = await self._run_sync(self._get_events_sync, time_min, time_max)
        return f"Events for the next 7 days:\n\n{result}"

    async def get_date_events(self, date_str: str) -> str:
        """Get events for a specific date (YYYY-MM-DD)."""
        try:
            date = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            return f"Invalid date format: {date_str}. Use YYYY-MM-DD."

        end = date + timedelta(days=1)
        time_min = date.isoformat() + self._tz_offset()
        time_max = end.isoformat() + self._tz_offset()
        result = await self._run_sync(self._get_events_sync, time_min, time_max)
        return f"Events for {date_str}:\n\n{result}"

    def _tz_offset(self) -> str:
        """Get the current UTC offset string (e.g. '+05:30')."""
        try:
            import zoneinfo
            from datetime import timezone
            tz = zoneinfo.ZoneInfo(self.timezone)
            offset = datetime.now(tz).utcoffset()
            if offset is None:
                return "Z"
            total_seconds = int(offset.total_seconds())
            hours, remainder = divmod(abs(total_seconds), 3600)
            minutes = remainder // 60
            sign = "+" if total_seconds >= 0 else "-"
            return f"{sign}{hours:02d}:{minutes:02d}"
        except Exception:
            return "Z"

    # Create events
    def _create_event_sync(self, title: str, date: str, time_str: str,
                           duration_minutes: int = 60, description: str = "",
                           location: str = "") -> str:
        """Create a calendar event (synchronous)."""
        try:
            service = self._get_service()

            # Parse date and time
            start_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration_minutes)

            event_body = {
                "summary": title,
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": self.timezone,
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": self.timezone,
                },
            }

            if description:
                event_body["description"] = description
            if location:
                event_body["location"] = location

            event = service.events().insert(
                calendarId="primary", body=event_body
            ).execute()

            logger.info("Created event: %s (ID: %s)", title, event["id"])
            return (
                f"Event created:\n"
                f"  Title: {title}\n"
                f"  Date: {date} at {time_str}\n"
                f"  Duration: {duration_minutes} minutes\n"
                f"  ID: {event['id']}"
            )

        except ValueError as exc:
            return f"Invalid date/time format: {exc}. Use YYYY-MM-DD and HH:MM."
        except HttpError as exc:
            logger.error("Calendar API error (create): %s", exc)
            return f"Calendar API error: {exc}"
        except Exception as exc:
            logger.error("Error creating event: %s", exc)
            return f"Error creating event: {exc}"

    async def create_event(self, title: str, date: str, time_str: str,
                           duration_minutes: int = 60, description: str = "") -> str:
        """Create a calendar event."""
        return await self._run_sync(
            self._create_event_sync, title, date, time_str, duration_minutes, description
        )

    # Create all-day events
    def _create_allday_sync(self, title: str, date: str, description: str = "") -> str:
        """Create an all-day event (synchronous)."""
        try:
            service = self._get_service()

            # Validate date
            datetime.strptime(date, "%Y-%m-%d")

            event_body = {
                "summary": title,
                "start": {"date": date},
                "end": {"date": date},
            }
            if description:
                event_body["description"] = description

            event = service.events().insert(
                calendarId="primary", body=event_body
            ).execute()

            logger.info("Created all-day event: %s (ID: %s)", title, event["id"])
            return (
                f"All-day event created:\n"
                f"  Title: {title}\n"
                f"  Date: {date}\n"
                f"  ID: {event['id']}"
            )

        except ValueError:
            return f"Invalid date format: {date}. Use YYYY-MM-DD."
        except HttpError as exc:
            logger.error("Calendar API error (create allday): %s", exc)
            return f"Calendar API error: {exc}"
        except Exception as exc:
            logger.error("Error creating all-day event: %s", exc)
            return f"Error creating all-day event: {exc}"

    async def create_allday_event(self, title: str, date: str, description: str = "") -> str:
        """Create an all-day event."""
        return await self._run_sync(self._create_allday_sync, title, date, description)

    # Update events
    def _update_event_sync(self, event_id: str, field: str, new_value: str) -> str:
        """Update a specific field of a calendar event (synchronous)."""
        try:
            service = self._get_service()

            # Get existing event
            event = service.events().get(
                calendarId="primary", eventId=event_id
            ).execute()

            field_lower = field.lower().strip()

            if field_lower in ("title", "summary"):
                event["summary"] = new_value
            elif field_lower == "description":
                event["description"] = new_value
            elif field_lower == "location":
                event["location"] = new_value
            elif field_lower in ("date", "start_date"):
                # Reschedule — keep same time, change date
                old_start = event.get("start", {})
                if "dateTime" in old_start:
                    old_dt = datetime.fromisoformat(old_start["dateTime"])
                    new_date = datetime.strptime(new_value, "%Y-%m-%d")
                    new_start = old_dt.replace(
                        year=new_date.year, month=new_date.month, day=new_date.day
                    )
                    # Calculate duration
                    old_end = datetime.fromisoformat(event["end"]["dateTime"])
                    duration = old_end - old_dt
                    new_end = new_start + duration

                    event["start"]["dateTime"] = new_start.isoformat()
                    event["end"]["dateTime"] = new_end.isoformat()
                else:
                    event["start"]["date"] = new_value
                    event["end"]["date"] = new_value
            elif field_lower in ("time", "start_time"):
                old_start = event.get("start", {})
                if "dateTime" in old_start:
                    old_dt = datetime.fromisoformat(old_start["dateTime"])
                    new_time = datetime.strptime(new_value, "%H:%M")
                    new_start = old_dt.replace(hour=new_time.hour, minute=new_time.minute)
                    old_end = datetime.fromisoformat(event["end"]["dateTime"])
                    duration = old_end - old_dt
                    new_end = new_start + duration

                    event["start"]["dateTime"] = new_start.isoformat()
                    event["end"]["dateTime"] = new_end.isoformat()
                else:
                    return "Cannot set time on an all-day event."
            else:
                return f"Unknown field: {field}. Use: title, description, location, date, time."

            updated = service.events().update(
                calendarId="primary", eventId=event_id, body=event
            ).execute()

            logger.info("Updated event %s: %s = %s", event_id, field, new_value)
            return f"Event updated:\n{self._format_event(updated)}"

        except HttpError as exc:
            logger.error("Calendar API error (update): %s", exc)
            return f"Calendar API error: {exc}"
        except Exception as exc:
            logger.error("Error updating event: %s", exc)
            return f"Error updating event: {exc}"

    async def update_event(self, event_id: str, field: str, new_value: str) -> str:
        """Update a specific field of a calendar event."""
        return await self._run_sync(self._update_event_sync, event_id, field, new_value)

    # Delete events
    def _delete_event_sync(self, event_id: str) -> str:
        """Delete a calendar event (synchronous)."""
        try:
            service = self._get_service()
            service.events().delete(
                calendarId="primary", eventId=event_id
            ).execute()
            logger.info("Deleted event %s", event_id)
            return f"Event {event_id} deleted."
        except HttpError as exc:
            logger.error("Calendar API error (delete): %s", exc)
            return f"Calendar API error: {exc}"
        except Exception as exc:
            logger.error("Error deleting event: %s", exc)
            return f"Error deleting event: {exc}"

    async def delete_event(self, event_id: str) -> str:
        """Delete a calendar event."""
        return await self._run_sync(self._delete_event_sync, event_id)

    # Set reminder
    def _set_reminder_sync(self, event_id: str, minutes_before: int) -> str:
        """Set a popup reminder on an event (synchronous)."""
        try:
            service = self._get_service()
            event = service.events().get(
                calendarId="primary", eventId=event_id
            ).execute()

            event["reminders"] = {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": minutes_before},
                ],
            }

            updated = service.events().update(
                calendarId="primary", eventId=event_id, body=event
            ).execute()

            title = updated.get("summary", "(No title)")
            logger.info("Set reminder on event %s: %d minutes before", event_id, minutes_before)
            return f"Reminder set: {minutes_before} minutes before '{title}'."

        except HttpError as exc:
            logger.error("Calendar API error (reminder): %s", exc)
            return f"Calendar API error: {exc}"
        except Exception as exc:
            logger.error("Error setting reminder: %s", exc)
            return f"Error setting reminder: {exc}"

    async def set_reminder(self, event_id: str, minutes_before: int) -> str:
        """Set a popup reminder on an event."""
        return await self._run_sync(self._set_reminder_sync, event_id, minutes_before)

    # Natural language event creation (via Ollama)
    async def create_from_natural_language(self, text: str) -> str:
        """Parse natural language with Ollama and create an event."""
        if not self.ollama:
            return "Ollama connector is not available for natural language parsing."

        today = datetime.now().strftime("%Y-%m-%d")
        day_name = datetime.now().strftime("%A")

        prompt = (
            f"Today is {day_name}, {today}. "
            f"Extract event details from this request and return ONLY a JSON object "
            f"with these keys: title, date (YYYY-MM-DD), time (HH:MM in 24h format), "
            f"duration_minutes (integer), description (optional string). "
            f"If no time specified, default to 09:00. If no duration, default to 60.\n\n"
            f"Request: {text}\n\n"
            f"Return ONLY the JSON object, no other text."
        )

        try:
            response = self.ollama.chat([{"role": "user", "content": prompt}])

            # Try to extract JSON from response
            response = response.strip()
            # Handle markdown code blocks
            if "```" in response:
                import re
                match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
                if match:
                    response = match.group(1)

            data = json.loads(response)

            title = data.get("title", "Untitled Event")
            date = data.get("date", today)
            time_str = data.get("time", "09:00")
            duration = int(data.get("duration_minutes", 60))
            description = data.get("description", "")

            result = await self.create_event(title, date, time_str, duration, description)
            return f"(Parsed from: \"{text}\")\n{result}"

        except json.JSONDecodeError:
            logger.error("Failed to parse Ollama response as JSON: %s", response)
            return (
                f"Could not parse the natural language request. "
                f"Please use the explicit format: "
                f"[ACTION:cal_create:title|date|time|duration|description]"
            )
        except Exception as exc:
            logger.error("Error in natural language event creation: %s", exc)
            return f"Error creating event from natural language: {exc}"

    # Upcoming events (used by scheduler)
    def _get_upcoming_sync(self, minutes_ahead: int = 45) -> list[dict]:
        """Get events starting within the next N minutes (synchronous)."""
        try:
            service = self._get_service()
            now = datetime.now()
            time_min = now.isoformat() + self._tz_offset_sync()
            time_max = (now + timedelta(minutes=minutes_ahead)).isoformat() + self._tz_offset_sync()

            events_result = service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                timeZone=self.timezone,
            ).execute()

            return events_result.get("items", [])

        except Exception as exc:
            logger.error("Error fetching upcoming events: %s", exc)
            return []

    def _tz_offset_sync(self) -> str:
        """Synchronous version of timezone offset calc."""
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(self.timezone)
            offset = datetime.now(tz).utcoffset()
            if offset is None:
                return "Z"
            total_seconds = int(offset.total_seconds())
            hours, remainder = divmod(abs(total_seconds), 3600)
            minutes = remainder // 60
            sign = "+" if total_seconds >= 0 else "-"
            return f"{sign}{hours:02d}:{minutes:02d}"
        except Exception:
            return "Z"

    async def get_upcoming_events(self, minutes_ahead: int = 45) -> list[dict]:
        """Get events starting within the next N minutes."""
        return await self._run_sync(self._get_upcoming_sync, minutes_ahead)

    # Today's event count (used by scheduler for morning briefing)
    def _get_today_count_sync(self) -> int:
        """Get count of today's events (synchronous)."""
        try:
            service = self._get_service()
            now = datetime.now()
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            offset = self._tz_offset_sync()

            events_result = service.events().list(
                calendarId="primary",
                timeMin=start.isoformat() + offset,
                timeMax=end.isoformat() + offset,
                singleEvents=True,
                timeZone=self.timezone,
            ).execute()

            return len(events_result.get("items", []))

        except Exception as exc:
            logger.error("Error getting today's event count: %s", exc)
            return 0

    async def get_today_count(self) -> int:
        """Get count of today's events."""
        return await self._run_sync(self._get_today_count_sync)
