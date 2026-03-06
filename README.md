# OpenPaw - Personal Desktop AI Agent

A fully autonomous desktop agent that runs 24/7 on your Windows machine, accessible via Telegram, powered by a local Ollama LLM. Not just a chatbot — it can control your system, manage files, run commands, open apps, and act proactively with safety mechanisms on every destructive action.

## Architecture

```
Telegram (you) <---> Python Backend <---> Ollama (local LLM)
                         |
              File System / Shell / Apps / Monitoring
```

## Setup

### 1. Prerequisites

- **Python 3.10+** installed and on PATH
- **Ollama** installed and running (`ollama serve`)
- A model pulled: `ollama pull qwen2.5:3b`
- A **Telegram Bot Token** from [@BotFather](https://t.me/BotFather)
- Your **Telegram User ID** from [@userinfobot](https://t.me/userinfobot)

### 2. Configure

Edit `config.env` and fill in:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USER_ID=your_numeric_user_id
```

Other settings you can customize:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Model to use |
| `DATA_DIR` | `d:/OpenPaw/data` | Where memory/logs are stored |
| `ALLOWED_DIRS` | `~/Desktop,~/Downloads,~/Documents,d:/Projects` | Whitelisted working directories |
| `HEARTBEAT_INTERVAL` | `5` | Minutes between proactive checks |
| `BATTERY_ALERT_THRESHOLD` | `20` | Battery % to trigger alert |
| `COMMAND_TIMEOUT` | `30` | Seconds before shell commands timeout |
| `CONFIRMATION_TIMEOUT` | `30` | Seconds to wait for yes/no confirmation |

### 3. Run

**Option A — Double-click:**
```
start_agent.bat
```

**Option B — Manual:**
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### 4. (Optional) Run on Windows Startup

1. Press `Win+R`, type `shell:startup`, press Enter
2. Create a shortcut to `start_agent.bat` in that folder

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Initialize the agent |
| `/status` | CPU, RAM, disk, battery info |
| `/clear` | Reset conversation memory |
| `/help` | Show all commands |

Or talk naturally:
- "List files on my Desktop"
- "Open Chrome"
- "What's my RAM usage?"
- "Run `dir C:\Users`"
- "Delete test.txt on Desktop"
- "Remind me in 10 minutes to take a break"
- "Search for .py files in my Projects"
- "Kill the process using PID 1234"

## Safety Layers

### Path Blacklist
The agent cannot touch these directories under any circumstance:
- `C:/Windows`, `C:/Program Files`, `C:/Program Files (x86)`, `C:/System32`
- Any path containing system keywords (`system32`, `winsxs`, `boot`, etc.)

### Command Blacklist
These shell patterns are blocked:
- `format`, `rmdir /s`, `del /f /s /q`, `rm -rf`, `shutdown`
- `reg delete`, `diskpart`, `bcdedit`, `sfc`, `dism`

### Confirmation Required
Destructive actions **never** execute immediately. The bot asks:
> "Confirm? Reply yes to proceed or no to cancel."

This applies to: file deletion, moves, renames, shell commands, killing processes, writing files. 30-second auto-cancel timeout.

### Owner-Only Access
Only the configured `ALLOWED_USER_ID` can interact. Messages from all other users are silently ignored.

## Project Structure

```
OpenPaw/
  main.py               # Entry point, config loading, crash-recovery loop
  telegram_handler.py   # Telegram bot, commands, action parser, confirmation flow
  ollama_connector.py   # Ollama REST API integration
  file_tools.py         # File system operations with safety checks
  system_tools.py       # System monitoring, app/process control, shell execution
  memory_manager.py     # Persistent conversation history and preferences
  scheduler.py          # Proactive heartbeat: battery, reminders, folder watch
  safety.py             # Path blacklist, command blacklist, confirmation manager
  config.env            # Configuration (tokens, settings)
  requirements.txt      # Python dependencies
  start_agent.bat       # Windows startup script
  data/                 # Created at runtime
    conversation_history.json
    preferences.json
    reminders.json
    agent.log
```

## Logs

All actions, commands, errors, and confirmations are logged to `data/agent.log` with timestamps. If anything goes wrong, the bot sends "Something went wrong, check agent.log" and auto-restarts.
