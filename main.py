"""
main.py - Entry point for the OpenPaw agent.

Loads configuration, wires all modules together, and starts the bot
with a crash-recovery loop so it runs 24/7.
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# ------------------------------------------------------------------
# Load environment variables
# ------------------------------------------------------------------
ENV_PATH = os.path.join(os.path.dirname(__file__), "config.env")
load_dotenv(ENV_PATH)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "5"))
BATTERY_ALERT_THRESHOLD = int(os.getenv("BATTERY_ALERT_THRESHOLD", "20"))
COMMAND_TIMEOUT = int(os.getenv("COMMAND_TIMEOUT", "30"))
CONFIRMATION_TIMEOUT = int(os.getenv("CONFIRMATION_TIMEOUT", "30"))

# RAG settings
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").lower() in ("true", "1", "yes")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
RAG_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.3"))

# Parse allowed directories – resolve each relative ~ path to an absolute Windows path
_raw_dirs = os.getenv("ALLOWED_DIRS", "~/Desktop,~/Downloads,~/Documents")
ALLOWED_DIRS = [
    str(Path(os.path.expanduser(d.strip())).resolve())
    for d in _raw_dirs.split(",")
    if d.strip()
]

# ------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------
LOG_FILE = os.path.join(DATA_DIR, "agent.log")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("openpaw.main")


def validate_config():
    """Ensure required configuration is present."""
    errors = []
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        errors.append("TELEGRAM_BOT_TOKEN is not set in config.env")
    if ALLOWED_USER_ID == 0:
        errors.append("ALLOWED_USER_ID is not set in config.env")
    if errors:
        for e in errors:
            logger.error("CONFIG ERROR: %s", e)
        print("\n=== Configuration errors ===")
        for e in errors:
            print(f"  - {e}")
        print(f"\nEdit {ENV_PATH} and fill in the required values.")
        sys.exit(1)


async def run_bot():
    """Initialize and run the bot with the scheduler."""
    from ollama_connector import OllamaConnector
    from memory_manager import MemoryManager
    from telegram_handler import TelegramHandler
    from scheduler import ProactiveScheduler

    # Initialize modules
    from browser_tools import BrowserManager
    from user_profile import UserProfile

    user_profile = UserProfile(data_dir=DATA_DIR)
    browser = BrowserManager(data_dir=DATA_DIR)
    ollama = OllamaConnector(base_url=OLLAMA_BASE_URL, model=OLLAMA_MODEL, user_profile=user_profile)
    memory = MemoryManager(data_dir=DATA_DIR)

    # Initialize RAG embedding store (if enabled)
    embedding_store = None
    if RAG_ENABLED:
        try:
            from embedding_store import EmbeddingStore
            embedding_store = EmbeddingStore(
                data_dir=DATA_DIR,
                ollama_base_url=OLLAMA_BASE_URL,
                embed_model=EMBED_MODEL,
                top_k=RAG_TOP_K,
                similarity_threshold=RAG_SIMILARITY_THRESHOLD,
            )
            # Backfill existing conversation history on first run
            if embedding_store.get_entry_count() == 0:
                existing_history = memory.get_all_history()
                if existing_history:
                    count = embedding_store.backfill_from_history(existing_history)
                    logger.info("Backfilled %d messages into embedding store", count)
            logger.info(
                "RAG enabled: model=%s, top_k=%d, threshold=%.2f, entries=%d",
                EMBED_MODEL, RAG_TOP_K, RAG_SIMILARITY_THRESHOLD,
                embedding_store.get_entry_count(),
            )
        except ImportError:
            logger.warning("numpy not installed — RAG disabled. Install with: pip install numpy")
            embedding_store = None
        except Exception as exc:
            logger.warning("Failed to initialize embedding store — RAG disabled: %s", exc)
            embedding_store = None

    handler = TelegramHandler(
        token=TELEGRAM_BOT_TOKEN,
        allowed_user_id=ALLOWED_USER_ID,
        ollama=ollama,
        memory=memory,
        allowed_dirs=ALLOWED_DIRS,
        command_timeout=COMMAND_TIMEOUT,
        confirmation_timeout=CONFIRMATION_TIMEOUT,
        embedding_store=embedding_store,
        rag_top_k=RAG_TOP_K,
        browser=browser,
        user_profile=user_profile,
    )

    app = handler.build()

    # Check Ollama availability on startup
    if ollama.is_available():
        logger.info("Ollama is reachable at %s (model: %s)", OLLAMA_BASE_URL, OLLAMA_MODEL)
    else:
        logger.warning("Ollama is NOT reachable at %s — agent will still start but LLM calls will fail", OLLAMA_BASE_URL)

    # Verify embedding model is available
    if embedding_store:
        test_result = embedding_store.embed_text("test")
        if test_result is not None:
            logger.info("Embedding model '%s' is ready (dim=%d)", EMBED_MODEL, len(test_result))
        else:
            logger.warning(
                "Embedding model '%s' is not available. Pull it with: ollama pull %s",
                EMBED_MODEL, EMBED_MODEL,
            )
            logger.warning("RAG will be disabled until the model is available.")

    # Initialize and start the scheduler
    # Watch the user's Downloads folder for new files
    downloads_dir = str(Path.home() / "Downloads")
    watch_folders = [downloads_dir] if os.path.isdir(downloads_dir) else []

    scheduler = ProactiveScheduler(
        memory_manager=memory,
        send_fn=handler.send_to_user,
        owner_id=ALLOWED_USER_ID,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        battery_threshold=BATTERY_ALERT_THRESHOLD,
        watch_folders=watch_folders,
    )

    # Start the bot with long polling
    logger.info("Starting OpenPaw agent...")
    logger.info("Allowed user ID: %s", ALLOWED_USER_ID)
    logger.info("Allowed directories: %s", ALLOWED_DIRS)
    logger.info("Data directory: %s", DATA_DIR)

    async with app:
        await app.start()
        await scheduler.start()

        logger.info("OpenPaw agent is ONLINE. Listening for messages...")
        print("\n=== OpenPaw Agent is ONLINE ===")
        print(f"  Bot token:   ...{TELEGRAM_BOT_TOKEN[-8:]}")
        print(f"  Owner ID:    {ALLOWED_USER_ID}")
        print(f"  Ollama:      {OLLAMA_BASE_URL} ({OLLAMA_MODEL})")
        print(f"  Data dir:    {DATA_DIR}")
        print(f"  Log file:    {LOG_FILE}")
        rag_status = "enabled" if embedding_store else "disabled"
        if embedding_store:
            rag_status += f" ({EMBED_MODEL}, {embedding_store.get_entry_count()} entries)"
        print(f"  RAG:         {rag_status}")
        print("  Press Ctrl+C to stop.\n")

        # Start polling
        await app.updater.start_polling(drop_pending_updates=True)

        # Keep running until interrupted
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await scheduler.stop()
            await app.updater.stop()
            await app.stop()


def main():
    """Main entry point with crash-recovery loop."""
    validate_config()

    while True:
        try:
            asyncio.run(run_bot())
        except KeyboardInterrupt:
            logger.info("Agent stopped by user (Ctrl+C).")
            print("\nAgent stopped.")
            break
        except Exception as exc:
            logger.exception("Agent crashed — restarting in 10 seconds...")
            print(f"\n[ERROR] Agent crashed: {exc}")
            print("Restarting in 10 seconds... (Ctrl+C to stop)")
            try:
                time.sleep(10)
            except KeyboardInterrupt:
                logger.info("Agent stopped during restart wait.")
                print("\nAgent stopped.")
                break


if __name__ == "__main__":
    main()
