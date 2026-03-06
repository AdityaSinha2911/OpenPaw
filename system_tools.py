"""
system_tools.py - System monitoring, application/process control, shell execution.

Uses psutil for monitoring and subprocess for command execution.
All shell commands are validated through the safety module.
"""

import logging
import os
import platform
import subprocess
import webbrowser
from pathlib import Path

import psutil

from safety import is_command_blocked, is_process_protected, is_path_blacklisted, get_all_drives

logger = logging.getLogger("openpaw.system_tools")


# ------------------------------------------------------------------
# System monitoring
# ------------------------------------------------------------------

def get_system_info() -> str:
    """Return a formatted summary of current system resource usage."""
    lines = []

    # CPU
    cpu_pct = psutil.cpu_percent(interval=1)
    cpu_count = psutil.cpu_count()
    lines.append(f"CPU: {cpu_pct}% ({cpu_count} cores)")

    # Memory
    mem = psutil.virtual_memory()
    lines.append(
        f"RAM: {mem.percent}% used "
        f"({mem.used / (1024**3):.1f} GB / {mem.total / (1024**3):.1f} GB)"
    )

    # Disk
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            lines.append(
                f"Disk {part.device}: {usage.percent}% used "
                f"({usage.used / (1024**3):.1f} GB / {usage.total / (1024**3):.1f} GB)"
            )
        except PermissionError:
            pass

    # Battery
    batt = psutil.sensors_battery()
    if batt:
        plug = "plugged in" if batt.power_plugged else "on battery"
        lines.append(f"Battery: {batt.percent}% ({plug})")

    # Network
    net = psutil.net_io_counters()
    lines.append(
        f"Network: sent {net.bytes_sent / (1024**2):.1f} MB, "
        f"recv {net.bytes_recv / (1024**2):.1f} MB"
    )

    # Uptime
    import time
    boot = psutil.boot_time()
    uptime_secs = time.time() - boot
    hours, rem = divmod(int(uptime_secs), 3600)
    minutes, _ = divmod(rem, 60)
    lines.append(f"Uptime: {hours}h {minutes}m")

    lines.append(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")

    return "\n".join(lines)


def get_battery_percent() -> int | None:
    """Return battery percent or None if no battery."""
    batt = psutil.sensors_battery()
    return int(batt.percent) if batt else None


def is_on_battery() -> bool:
    """Return True if running on battery (not plugged in)."""
    batt = psutil.sensors_battery()
    if batt is None:
        return False
    return not batt.power_plugged


# ------------------------------------------------------------------
# Process management
# ------------------------------------------------------------------

def list_processes(top_n: int = 30) -> str:
    """List top processes by memory usage."""
    procs = []
    for proc in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
        try:
            info = proc.info
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    procs.sort(key=lambda p: p.get("memory_percent", 0) or 0, reverse=True)
    procs = procs[:top_n]

    lines = [f"{'PID':<8} {'CPU%':<7} {'MEM%':<7} {'Name'}"]
    lines.append("-" * 45)
    for p in procs:
        pid = p.get("pid", "?")
        name = p.get("name", "?")
        mem_pct = p.get("memory_percent", 0) or 0
        cpu_pct = p.get("cpu_percent", 0) or 0
        lines.append(f"{pid:<8} {cpu_pct:<7.1f} {mem_pct:<7.1f} {name}")

    return "\n".join(lines)


def kill_process(pid: int) -> str:
    """Kill a process by PID."""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        if is_process_protected(name):
            logger.warning("Attempted to kill protected process: %s (PID %d)", name, pid)
            return "This process is critical to Windows and cannot be terminated."
        proc.terminate()
        proc.wait(timeout=5)
        logger.info("Killed process: %s (PID %d)", name, pid)
        return f"Terminated: {name} (PID {pid})"
    except psutil.NoSuchProcess:
        return f"No process found with PID {pid}"
    except psutil.AccessDenied:
        return f"Access denied — cannot kill PID {pid}"
    except psutil.TimeoutExpired:
        try:
            psutil.Process(pid).kill()
            logger.info("Force-killed process PID %d", pid)
            return f"Force-killed PID {pid}"
        except Exception as exc:
            return f"Failed to force-kill PID {pid}: {exc}"
    except Exception as exc:
        logger.exception("Error killing process %d", pid)
        return f"Error killing process: {exc}"


def get_process_name(pid: int) -> str | None:
    """Get the name of a process by PID, or None."""
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


# ------------------------------------------------------------------
# System-wide file / application search
# ------------------------------------------------------------------

def find_file_or_app(name: str, max_results: int = 50) -> str:
    """Search for a file, folder, or application by name across all drives.

    Drives are enumerated dynamically at runtime.  Protected system
    directories are skipped automatically.
    """
    drives = get_all_drives()
    matches: list[str] = []
    name_lower = name.lower()

    for drive in drives:
        root_path = Path(drive).resolve()
        try:
            for root, dirs, files in os.walk(root_path, topdown=True, onerror=lambda e: None):
                # Skip protected/blacklisted directories in-place
                if is_path_blacklisted(root):
                    dirs.clear()
                    continue
                root_p = Path(root)
                # Match sub-directory names
                for d in list(dirs):
                    if name_lower in d.lower():
                        matches.append(str(root_p / d))
                # Match file names
                for f in files:
                    if name_lower in f.lower():
                        matches.append(str(root_p / f))
                if len(matches) >= max_results:
                    break
        except (PermissionError, OSError):
            pass
        if len(matches) >= max_results:
            break

    if not matches:
        return f"Not found on this system: {name}"
    result = "\n".join(matches[:max_results])
    if len(matches) >= max_results:
        result += f"\n... (showing first {max_results} results)"
    return result


# ------------------------------------------------------------------
# Application launching
# ------------------------------------------------------------------

def open_application(app_name: str) -> str:
    """Try to open an application by name."""
    try:
        # Try running directly (works for apps on PATH)
        subprocess.Popen(
            app_name,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Opened application: %s", app_name)
        return f"Opened: {app_name}"
    except Exception as exc:
        logger.exception("Error opening application %s", app_name)
        return f"Error opening application: {exc}"


def open_url(url: str) -> str:
    """Open a URL in the default browser."""
    try:
        webbrowser.open(url)
        logger.info("Opened URL: %s", url)
        return f"Opened URL: {url}"
    except Exception as exc:
        logger.exception("Error opening URL %s", url)
        return f"Error opening URL: {exc}"


# ------------------------------------------------------------------
# Shell command execution
# ------------------------------------------------------------------

def run_command(command: str, timeout: int = 30) -> str:
    """Execute a shell command and return the output.

    The command is first checked against the safety blocklist.
    """
    if is_command_blocked(command):
        return "This command is blocked for safety."

    logger.info("Executing command: %s", command)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output_parts = []
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.stderr.strip():
            output_parts.append(f"STDERR:\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"(exit code {result.returncode})")

        output = "\n".join(output_parts) if output_parts else "(no output)"

        # Truncate very long output
        if len(output) > 3000:
            output = output[:3000] + "\n\n... (output truncated)"

        return output

    except subprocess.TimeoutExpired:
        logger.warning("Command timed out after %ds: %s", timeout, command)
        return f"Command timed out after {timeout} seconds."
    except Exception as exc:
        logger.exception("Error running command: %s", command)
        return f"Error running command: {exc}"
