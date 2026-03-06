"""
telegram_handler.py - Telegram bot interface with commands, action parsing,
and confirmation flow.

Uses python-telegram-bot v20+ (async) with long polling.
Only the owner (ALLOWED_USER_ID) can interact; all other messages are ignored.
"""

import asyncio
import logging
import re
import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from safety import (
    ConfirmationManager,
    is_path_blacklisted,
    is_path_in_allowed_dirs,
)
from ollama_connector import OllamaConnector
from memory_manager import MemoryManager
from embedding_store import EmbeddingStore
from file_tools import (
    read_file,
    write_file,
    delete_file,
    delete_folder,
    move_path,
    list_directory,
    search_files,
    open_file,
    find_path_system_wide,
    scan_temp_files,
    scan_app_sizes,
)
from system_tools import (
    get_system_info,
    list_processes,
    kill_process,
    get_process_name,
    open_application,
    open_url,
    run_command,
    find_file_or_app,
)

logger = logging.getLogger("openpaw.telegram")

# Regex to parse action tags from Ollama responses
ACTION_RE = re.compile(r"\[ACTION:([^\]]+)\]")


class TelegramHandler:
    def __init__(
        self,
        token: str,
        allowed_user_id: int,
        ollama: OllamaConnector,
        memory: MemoryManager,
        allowed_dirs: list[str],
        command_timeout: int = 30,
        confirmation_timeout: int = 30,
        embedding_store: EmbeddingStore | None = None,
        rag_top_k: int = 5,
    ):
        self.token = token
        self.allowed_user_id = allowed_user_id
        self.ollama = ollama
        self.memory = memory
        self.allowed_dirs = allowed_dirs
        self.command_timeout = command_timeout
        self.embedding_store = embedding_store
        self.rag_top_k = rag_top_k

        self.confirm = ConfirmationManager(timeout=confirmation_timeout)
        self.app: Application | None = None

    # ------------------------------------------------------------------
    # Authorization guard
    # ------------------------------------------------------------------
    def _is_authorized(self, update: Update) -> bool:
        user = update.effective_user
        if user is None or user.id != self.allowed_user_id:
            logger.warning(
                "Rejected message from unauthorized user: %s (ID %s)",
                getattr(user, "username", "?"),
                getattr(user, "id", "?"),
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Helper: send a message, splitting if too long for Telegram
    # ------------------------------------------------------------------
    async def _send(self, update_or_id, text: str, context: ContextTypes.DEFAULT_TYPE | None = None, **kwargs):
        """Send a text message, handling the 4096-char Telegram limit."""
        if isinstance(update_or_id, int):
            chat_id = update_or_id
        else:
            chat_id = update_or_id.effective_chat.id

        # Get the bot instance
        if context:
            bot = context.bot
        elif self.app:
            bot = self.app.bot
        else:
            logger.error("No bot instance available to send message")
            return

        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, **kwargs)
            except Exception as exc:
                logger.exception("Error sending message: %s", exc)

    # Wrapper used by ConfirmationManager and Scheduler
    async def send_to_user(self, user_id: int, text: str):
        """Send a message to a user by ID (used by scheduler/confirmation)."""
        if self.app:
            try:
                await self.app.bot.send_message(chat_id=user_id, text=text)
            except Exception as exc:
                logger.exception("Error sending to user %s: %s", user_id, exc)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        await self._send(
            update,
            "OpenPaw Agent is online.\n\n"
            "I'm your personal desktop agent. I can manage files, run commands, "
            "open apps, monitor your system, and more.\n\n"
            "Commands:\n"
            "/start - Initialize the agent\n"
            "/status - System resource info\n"
            "/clear - Reset conversation memory\n"
            "/help - Show all commands\n\n"
            "Or just talk to me naturally!",
            context=context,
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        await self._send(
            update,
            "OpenPaw Agent - Commands:\n\n"
            "/start - Initialize the agent\n"
            "/status - CPU, RAM, disk, battery info\n"
            "/clear - Reset conversation memory\n"
            "/help - Show this message\n\n"
            "Natural language examples:\n"
            '- "List files on my Desktop"\n'
            '- "Open Chrome"\n'
            '- "What\'s my RAM usage?"\n'
            '- "Run dir C:\\Users"\n'
            '- "Delete the file test.txt on Desktop"\n'
            '- "Remind me in 10 minutes to take a break"\n'
            '- "Search for .py files in my Projects"\n',
            context=context,
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        info = get_system_info()
        await self._send(update, f"System Status:\n\n{info}", context=context)

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        self.memory.clear_history(self.allowed_user_id)
        await self._send(update, "Conversation memory cleared. Long-term memory preserved.", context=context)

    async def cmd_forget(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        self.memory.clear_history(self.allowed_user_id)
        if self.embedding_store:
            self.embedding_store.clear(user_id=self.allowed_user_id)
        await self._send(
            update,
            "All memory cleared including long-term memory. Starting fresh.",
            context=context,
        )

    # ------------------------------------------------------------------
    # RAG context formatting
    # ------------------------------------------------------------------
    def _format_rag_context(self, results: list[dict], recent_messages: list[dict]) -> str | None:
        """Format RAG search results into a context string,
        excluding messages already in the recent conversation window."""
        recent_contents = {m["content"] for m in recent_messages}
        filtered = [r for r in results if r["content"] not in recent_contents]
        if not filtered:
            return None

        lines = []
        for r in filtered:
            role_label = "User" if r["role"] == "user" else "Assistant"
            age = time.time() - r["timestamp"]
            if age < 3600:
                age_str = f"{int(age / 60)} minutes ago"
            elif age < 86400:
                age_str = f"{int(age / 3600)} hours ago"
            else:
                age_str = f"{int(age / 86400)} days ago"

            content = r["content"][:500]
            lines.append(f"[{age_str}]")
            lines.append(f"{role_label}: {content}")
            lines.append("")

        context_str = "\n".join(lines).strip()
        if len(context_str) > 2000:
            context_str = context_str[:2000] + "\n... (context truncated)"
        return context_str

    # ------------------------------------------------------------------
    # Main message handler
    # ------------------------------------------------------------------
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        text = update.message.text
        if not text:
            return

        user_id = update.effective_user.id

        # Check if this is a response to a pending confirmation
        if self.confirm.has_pending(user_id):
            result = await self.confirm.handle_response(user_id, text)
            if result:
                await self._send(update, result, context=context)
            return

        # Add user message to memory
        self.memory.add_message(user_id, "user", text)

        # Get conversation history for Ollama
        messages = self.memory.get_ollama_messages(user_id)

        # RAG retrieval: find relevant past context
        rag_context = None
        if self.embedding_store:
            rag_results = self.embedding_store.search_by_text(
                text, top_k=self.rag_top_k, user_id=user_id,
            )
            if rag_results:
                rag_context = self._format_rag_context(rag_results, messages)

        # Call Ollama (with RAG context if available)
        reply = self.ollama.chat(messages, rag_context=rag_context)

        # Save assistant reply to memory
        self.memory.add_message(user_id, "assistant", reply)

        # Store both messages in embedding store for future retrieval
        if self.embedding_store:
            self.embedding_store.add_entry(user_id, "user", text, time.time())
            self.embedding_store.add_entry(user_id, "assistant", reply, time.time())

        # Parse for action tags
        action_match = ACTION_RE.search(reply)
        if action_match:
            action_raw = action_match.group(1)
            # Remove the action tag from the displayed reply
            display_reply = ACTION_RE.sub("", reply).strip()
            if display_reply:
                await self._send(update, display_reply, context=context)

            await self._execute_action(update, context, action_raw, user_id)
        else:
            await self._send(update, reply, context=context)

    # ------------------------------------------------------------------
    # Action execution engine
    # ------------------------------------------------------------------
    async def _execute_action(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        action_raw: str,
        user_id: int,
    ):
        """Parse and execute an action tag from Ollama's response."""
        parts = action_raw.split(":", 1)
        action_name = parts[0].strip().lower()
        args_str = parts[1] if len(parts) > 1 else ""

        logger.info("Action: %s | Args: %s", action_name, args_str)

        # ---------- Non-destructive actions (execute immediately) ----------

        if action_name == "read_file":
            result = read_file(args_str.strip(), self.allowed_dirs)
            await self._send(update, result, context=context)

        elif action_name == "list_dir":
            result = list_directory(args_str.strip(), self.allowed_dirs)
            await self._send(update, result, context=context)

        elif action_name == "search_files":
            parts = args_str.split("|", 1)
            if len(parts) == 2:
                result = search_files(parts[0].strip(), parts[1].strip(), self.allowed_dirs)
            else:
                result = "Invalid search format."
            await self._send(update, result, context=context)

        elif action_name == "system_info":
            result = get_system_info()
            await self._send(update, result, context=context)

        elif action_name == "list_processes":
            result = list_processes()
            await self._send(update, result, context=context)

        elif action_name == "open_url":
            result = open_url(args_str.strip())
            await self._send(update, result, context=context)

        elif action_name == "set_reminder":
            parts = args_str.split("|", 1)
            if len(parts) == 2:
                try:
                    minutes = float(parts[0].strip())
                    text = parts[1].strip()
                    trigger = time.time() + minutes * 60
                    self.memory.add_reminder(user_id, text, trigger)
                    result = f"Reminder set: '{text}' in {minutes} minutes."
                except ValueError:
                    result = "Invalid reminder format."
            else:
                result = "Invalid reminder format."
            await self._send(update, result, context=context)

        # ---------- Destructive actions (require confirmation) ----------

        elif action_name == "delete_file":
            path = args_str.strip()
            if is_path_blacklisted(path):
                await self._send(update, "This path is protected and cannot be modified.", context=context)
                return

            async def do_delete():
                return delete_file(path, self.allowed_dirs)

            await self.confirm.request_confirmation(
                user_id,
                f"Delete file: {path}",
                do_delete,
                self.send_to_user,
            )

        elif action_name == "delete_folder":
            path = args_str.strip()
            if is_path_blacklisted(path):
                await self._send(update, "This path is protected and cannot be modified.", context=context)
                return

            async def do_delete_folder():
                return delete_folder(path, self.allowed_dirs)

            await self.confirm.request_confirmation(
                user_id,
                f"Delete folder (and ALL contents): {path}",
                do_delete_folder,
                self.send_to_user,
            )

        elif action_name == "move":
            parts = args_str.split("|", 1)
            if len(parts) != 2:
                await self._send(update, "Invalid move format.", context=context)
                return
            src, dst = parts[0].strip(), parts[1].strip()
            for p in (src, dst):
                if is_path_blacklisted(p):
                    await self._send(update, "This path is protected and cannot be modified.", context=context)
                    return

            async def do_move():
                return move_path(src, dst, self.allowed_dirs)

            await self.confirm.request_confirmation(
                user_id,
                f"Move: {src} -> {dst}",
                do_move,
                self.send_to_user,
            )

        elif action_name == "write_file":
            parts = args_str.split("|", 1)
            if len(parts) != 2:
                await self._send(update, "Invalid write format.", context=context)
                return
            path, content = parts[0].strip(), parts[1]
            if is_path_blacklisted(path):
                await self._send(update, "This path is protected and cannot be modified.", context=context)
                return

            async def do_write():
                return write_file(path, content, self.allowed_dirs)

            await self.confirm.request_confirmation(
                user_id,
                f"Write to file: {path} ({len(content)} chars)",
                do_write,
                self.send_to_user,
            )

        elif action_name == "open_file":
            path = args_str.strip()
            result = open_file(path, self.allowed_dirs)
            await self._send(update, result, context=context)

        elif action_name == "open_app":
            result = open_application(args_str.strip())
            await self._send(update, result, context=context)

        elif action_name in ("find_path", "find_file", "find_app"):
            query = args_str.strip()
            if not query:
                await self._send(update, "Please provide a name to search for.", context=context)
                return
            result = find_path_system_wide(query)
            await self._send(update, result, context=context)

        elif action_name in ("find_app_installed", "search_app"):
            query = args_str.strip()
            if not query:
                await self._send(update, "Please provide an application name to search for.", context=context)
                return
            result = find_file_or_app(query)
            await self._send(update, result, context=context)
        elif action_name == "scan_temp":
            result = scan_temp_files()
            await self._send(update, result, context=context)

        elif action_name == "scan_apps":
            result = scan_app_sizes()
            await self._send(update, result, context=context)
        elif action_name == "scan_temp":
            result = scan_temp_files()
            await self._send(update, result, context=context)

        elif action_name == "scan_apps":
            result = scan_app_sizes()
            await self._send(update, result, context=context)

        elif action_name == "run_cmd":
            cmd = args_str.strip()

            async def do_run():
                return run_command(cmd, timeout=self.command_timeout)

            await self.confirm.request_confirmation(
                user_id,
                f"Run command: {cmd}",
                do_run,
                self.send_to_user,
            )

        elif action_name == "kill_process":
            try:
                pid = int(args_str.strip())
            except ValueError:
                await self._send(update, "Invalid PID.", context=context)
                return
            name = get_process_name(pid) or "unknown"

            async def do_kill():
                return kill_process(pid)

            await self.confirm.request_confirmation(
                user_id,
                f"Kill process: {name} (PID {pid})",
                do_kill,
                self.send_to_user,
            )

        else:
            await self._send(update, f"Unknown action: {action_name}", context=context)

    # ------------------------------------------------------------------
    # Bot lifecycle
    # ------------------------------------------------------------------
    def build(self) -> Application:
        """Build and return the telegram Application."""
        self.app = (
            Application.builder()
            .token(self.token)
            .build()
        )

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("clear", self.cmd_clear))
        self.app.add_handler(CommandHandler("forget", self.cmd_forget))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        return self.app