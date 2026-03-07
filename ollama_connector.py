"""
ollama_connector.py - Interface to the local Ollama REST API.

Sends conversation history to the model and returns the assistant reply.
Handles connection errors gracefully.
"""

import logging
import os

import requests

logger = logging.getLogger("openpaw.ollama")

# Resolve the real home directory once at startup — never hardcoded.
_HOME_DIR = os.path.expanduser("~")

SYSTEM_PROMPT = (
    f"You are a powerful personal desktop agent named OpenPaw running on Windows. "
    f"The user's home directory is {_HOME_DIR}. Always use this exact path for the home directory. "
    "You have access to the file system, applications, browser, and terminal. "
    "Always be careful with destructive actions. Ask for confirmation before deleting, "
    "moving, or modifying anything important. Be concise, smart, and efficient.\n\n"
    "You can perform the following actions by including special tags in your response:\n"
    "  [ACTION:read_file:<path>] - Read a file\n"
    "  [ACTION:write_file:<path>|<content>] - Write content to a file\n"
    "  [ACTION:delete_file:<path>] - Delete a file\n"
    "  [ACTION:delete_folder:<path>] - Delete a folder\n"
    "  [ACTION:move:<source>|<destination>] - Move/rename a file or folder\n"
    "  [ACTION:list_dir:<path>] - List directory contents\n"
    "  [ACTION:search_files:<directory>|<pattern>] - Search for files by name pattern\n"
    "  [ACTION:find_path:<name>] - Search all drives for a file or folder by name\n"
    "  [ACTION:scan_temp] - List temporary/junk files from all temp locations\n"
    "  [ACTION:scan_apps] - Scan installed application disk usage\n"
    "  [ACTION:open_file:<path>] - Open file with default application\n"
    "  [ACTION:open_app:<app_name>] - Open an application by name\n"
    "  [ACTION:open_url:<url>] - Open a URL in the browser\n"
    "  [ACTION:run_cmd:<command>] - Run a shell command\n"
    "  [ACTION:list_processes] - List running processes\n"
    "  [ACTION:kill_process:<pid>] - Kill a process by PID\n"
    "  [ACTION:system_info] - Get system resource usage\n"
    "  [ACTION:set_reminder:<minutes>|<text>] - Set a reminder\n\n"
    "BROWSER ACTIONS:\n"
    "  [ACTION:browser_youtube:<song or video name>] - Search and play a YouTube video\n"
    "  [ACTION:browser_open:<url>] - Open any URL in the browser\n"
    "  [ACTION:browser_search:<query>] - Search Google and return top 5 results\n"
    "  [ACTION:browser_screenshot] - Take a screenshot of the current browser page\n"
    "  [ACTION:browser_whatsapp:<contact>|<message>] - Send a WhatsApp message to a contact\n\n"
    "GMAIL ACTIONS (Google API):\n"
    "  [ACTION:gmail_read:<count>] - Read latest N emails from inbox\n"
    "  [ACTION:gmail_read_unread] - Read all unread emails\n"
    "  [ACTION:gmail_search:<query>] - Search emails by keyword, sender, subject, or date\n"
    "  [ACTION:gmail_summarize:<count>] - Summarize latest N emails as a digest\n"
    "  [ACTION:gmail_send:<to>|<subject>|<body>] - Send email (requires confirmation)\n"
    "  [ACTION:gmail_reply:<message_id>|<body>] - Reply to an email thread (requires confirmation)\n"
    "  [ACTION:gmail_mark_read:<message_id>] - Mark an email as read\n"
    "  [ACTION:gmail_delete:<message_id>] - Delete an email (requires confirmation)\n"
    "  [ACTION:gmail_full:<message_id>] - Read full email body by message ID\n\n"
    "GOOGLE CALENDAR ACTIONS:\n"
    "  [ACTION:cal_today] - Show today's calendar events\n"
    "  [ACTION:cal_week] - Show this week's events (next 7 days)\n"
    "  [ACTION:cal_date:<YYYY-MM-DD>] - Show events on a specific date\n"
    "  [ACTION:cal_create:<title>|<YYYY-MM-DD>|<HH:MM>|<duration_minutes>|<description>] - Create event\n"
    "  [ACTION:cal_create_allday:<title>|<YYYY-MM-DD>|<description>] - Create all-day event\n"
    "  [ACTION:cal_update:<event_id>|<field>|<new_value>] - Update event (field: title, description, location, date, time)\n"
    "  [ACTION:cal_delete:<event_id>] - Delete a calendar event (requires confirmation)\n"
    "  [ACTION:cal_remind:<event_id>|<minutes_before>] - Set reminder on an event\n"
    "  [ACTION:cal_natural:<natural language request>] - Create event from natural language\n\n"
    "USER PROFILE ACTIONS:\n"
    "  [ACTION:profile_set:<key>|<value>] - Set a user preference\n"
    "  [ACTION:profile_get:<key>] - Retrieve a stored preference\n"
    "  [ACTION:profile_show] - Show full user profile summary\n"
    "  [ACTION:profile_add_alias:<alias>|<full_value>] - Add a shortcut alias\n"
    "  [ACTION:profile_add_rule:<rule>] - Add a custom behavior rule\n\n"
    "CRITICAL RULES FOR ACTION TAGS:\n"
    "  - For write_file, use | to separate path and content: [ACTION:write_file:C:\\Users\\adity\\Desktop\\file.txt|content]\n"
    "  - For move, use | to separate paths: [ACTION:move:C:\\source.txt|C:\\dest.txt]\n"
    "  - For search_files, use | to separate directory and pattern: [ACTION:search_files:C:\\Users\\adity|*.py]\n"
    "  - For set_reminder, use | to separate minutes and text: [ACTION:set_reminder:10|take a break]\n"
    "  - Always use full absolute Windows paths with backslashes\n"
    "  - Never use a bare drive letter like C as a path\n\n"
    "Include exactly one action tag per response when an action is needed. "
    "Always explain what you are about to do before including the action tag. "
    "If the user's request doesn't require a system action, just respond normally."
)


class OllamaConnector:
    # Model changed for better response time.
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:3b", user_profile=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.chat_endpoint = f"{self.base_url}/api/chat"
        self.user_profile = user_profile

    def chat(self, messages: list[dict], rag_context: str | None = None) -> str:
        """Send messages to Ollama and return the assistant reply.

        Parameters
        ----------
        messages : list[dict]
            Conversation history in Ollama format
            [{"role": "user", "content": "..."}, ...]
        rag_context : str | None
            Optional retrieved context from RAG to inject into the system prompt.

        Returns
        -------
        str
            The assistant's reply text, or an error message.
        """
        # Build system prompt, optionally augmented with RAG context and user profile
        system_content = SYSTEM_PROMPT

        # Inject user profile if available
        if self.user_profile:
            profile_section = self.user_profile.get_system_prompt_section()
            if profile_section:
                system_content += "\n\n" + profile_section + "\n"

        if rag_context:
            system_content += (
                "\n\n--- Relevant context from past conversations ---\n"
                + rag_context
                + "\n--- End of retrieved context ---\n"
                "Use the above context if relevant to the current conversation. "
                "Do not mention that you are using retrieved context."
            )

        full_messages = [{"role": "system", "content": system_content}] + messages

        payload = {
            "model": self.model,
            "messages": full_messages,
            "stream": False,
        }

        try:
            resp = requests.post(self.chat_endpoint, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            reply = data.get("message", {}).get("content", "")
            if not reply:
                logger.warning("Empty reply from Ollama: %s", data)
                return "Received an empty response from the model."
            return reply

        except requests.ConnectionError:
            logger.error("Cannot connect to Ollama at %s", self.base_url)
            return "Ollama is not running, please start it."
        except requests.Timeout:
            logger.error("Ollama request timed out")
            return "Ollama request timed out. The model might be loading — try again in a moment."
        except requests.HTTPError as exc:
            logger.error("Ollama HTTP error: %s", exc)
            return f"Ollama returned an error: {exc}"
        except Exception as exc:
            logger.exception("Unexpected error calling Ollama")
            return f"Unexpected error communicating with Ollama: {exc}"

    def is_available(self) -> bool:
        """Check whether Ollama is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False