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
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.1:8b"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.chat_endpoint = f"{self.base_url}/api/chat"

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
        # Build system prompt, optionally augmented with RAG context
        system_content = SYSTEM_PROMPT
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