"""
file_tools.py - File system operations with safety checks.

Read-only operations (read, list, search, open) have NO path restrictions.
Only destructive operations (write, delete, move) validate against the
safety module before proceeding.
"""

import os
import shutil
import logging
from pathlib import Path
from safety import is_path_blacklisted, is_path_in_allowed_dirs, is_sensitive_file, get_all_drives, get_windows_folder

logger = logging.getLogger("openpaw.file_tools")


# ------------------------------------------------------------------
# Path resolver – fixes incomplete or malformed paths from LLM
# ------------------------------------------------------------------

def resolve_user_path(path: str) -> str:
    """
    Resolve common shorthand paths the LLM might produce into full absolute paths.

    Examples:
        "desktop"           -> C:\\Users\\adity\\Desktop
        "desktop/adi.py"    -> C:\\Users\\adity\\Desktop\\adi.py
        "downloads"         -> C:\\Users\\adity\\Downloads
        "C"                 -> kept as-is (single drive letter, likely a bug)

    Always returns a clean absolute path string using backslashes.
    """
    home = Path(os.path.expanduser("~"))

    shortcuts = {
        "desktop":   home / "Desktop",
        "downloads": home / "Downloads",
        "documents": home / "Documents",
        "pictures":  home / "Pictures",
        "videos":    home / "Videos",
        "music":     home / "Music",
    }

    # Normalize separators for comparison
    normalized = path.strip().replace("\\", "/").lower()

    for key, full_path in shortcuts.items():
        if normalized == key:
            return str(full_path)
        if normalized.startswith(key + "/"):
            remainder = path.strip().replace("\\", "/")[len(key):].lstrip("/")
            return str(full_path / remainder)

    # If path looks like just a drive letter "C" or "D" with no filename, log warning
    if len(path.strip()) == 1 and path.strip().isalpha():
        logger.warning("Received bare drive letter as path: '%s' — likely LLM extraction bug", path)

    # Otherwise resolve as-is using pathlib (handles both slash styles)
    return str(Path(path).resolve())


def _check_path(target: str, allowed_dirs: list[str]) -> tuple[bool, str]:
    """Validate a path for write/destructive ops. Returns (ok, message)."""
    if is_path_blacklisted(target):
        return False, "This path is protected and cannot be modified."
    return True, ""


def _needs_extra_confirm(target: str, allowed_dirs: list[str]) -> bool:
    """True if path is outside allowed working dirs (extra confirmation)."""
    return not is_path_in_allowed_dirs(target, allowed_dirs)


# ------------------------------------------------------------------
# Read operations (non-destructive — no path restrictions, no confirmation)
# ------------------------------------------------------------------

def read_file(path: str, allowed_dirs: list[str]) -> str:
    """Read and return the contents of a text file. No path restrictions."""
    path = resolve_user_path(path)
    if is_sensitive_file(path):
        logger.warning("Blocked read of sensitive file: %s", path)
        return "Access denied: this file contains sensitive data and cannot be read."
    try:
        p = Path(path)
        if not p.exists():
            return f"File not found: {path}"
        if not p.is_file():
            return f"Not a file: {path}"
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > 4000:
            content = content[:4000] + f"\n\n... (truncated, {len(content)} chars total)"
        return content
    except Exception as exc:
        logger.exception("Error reading file %s", path)
        return f"Error reading file: {exc}"


def list_directory(path: str, allowed_dirs: list[str]) -> str:
    """List contents of a directory. No path restrictions."""
    path = resolve_user_path(path)
    try:
        p = Path(path)
        if not p.exists():
            return f"Directory not found: {path}"
        if not p.is_dir():
            return f"Not a directory: {path}"
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        lines = []
        for e in entries[:100]:  # cap at 100 entries
            prefix = "[DIR] " if e.is_dir() else "      "
            size = ""
            if e.is_file():
                try:
                    sz = e.stat().st_size
                    if sz < 1024:
                        size = f" ({sz} B)"
                    elif sz < 1024 * 1024:
                        size = f" ({sz / 1024:.1f} KB)"
                    else:
                        size = f" ({sz / (1024*1024):.1f} MB)"
                except OSError:
                    pass
            lines.append(f"{prefix}{e.name}{size}")
        result = "\n".join(lines)
        if len(entries) > 100:
            result += f"\n\n... and {len(entries) - 100} more items"
        return result or "(empty directory)"
    except Exception as exc:
        logger.exception("Error listing directory %s", path)
        return f"Error listing directory: {exc}"


def search_files(directory: str, pattern: str, allowed_dirs: list[str]) -> str:
    """Recursively search for files matching *pattern* (glob). No path restrictions.

    The pattern must be relative (no leading slash/backslash).  Any leading
    separators are stripped automatically so that rglob() never throws
    'Non-relative patterns are unsupported'.
    """
    try:
        p = Path(directory).resolve()
        if not p.exists():
            return f"Directory not found: {directory}"
        # rglob() requires a relative, non-absolute pattern.
        # Strip any leading path separators to make it safe.
        safe_pattern = pattern.lstrip("/\\")
        if not safe_pattern:
            return "Invalid search pattern."
        matches = list(p.rglob(safe_pattern))
        if not matches:
            return f"No files matching '{pattern}' in {directory}"
        lines = [str(m) for m in matches[:50]]
        result = "\n".join(lines)
        if len(matches) > 50:
            result += f"\n\n... and {len(matches) - 50} more matches"
        return result
    except Exception as exc:
        logger.exception("Error searching files in %s", directory)
        return f"Error searching files: {exc}"


# ------------------------------------------------------------------
# Write / Create (requires confirmation if overwriting)
# ------------------------------------------------------------------

def write_file(path: str, content: str, allowed_dirs: list[str]) -> str:
    """Write content to a file. Creates parent dirs if needed."""
    path = resolve_user_path(path)
    ok, msg = _check_path(path, allowed_dirs)
    if not ok:
        return msg
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        logger.info("Wrote file: %s (%d chars)", path, len(content))
        return f"File written: {path} ({len(content)} chars)"
    except Exception as exc:
        logger.exception("Error writing file %s", path)
        return f"Error writing file: {exc}"


# ------------------------------------------------------------------
# Destructive operations (always require confirmation)
# ------------------------------------------------------------------

def delete_file(path: str, allowed_dirs: list[str]) -> str:
    """Delete a single file."""
    path = resolve_user_path(path)
    ok, msg = _check_path(path, allowed_dirs)
    if not ok:
        return msg
    try:
        p = Path(path)
        if not p.exists():
            return f"File not found: {path}"
        if not p.is_file():
            return f"Not a file: {path}"
        p.unlink()
        logger.info("Deleted file: %s", path)
        return f"Deleted: {path}"
    except Exception as exc:
        logger.exception("Error deleting file %s", path)
        return f"Error deleting file: {exc}"


def delete_folder(path: str, allowed_dirs: list[str]) -> str:
    """Delete a folder and all its contents."""
    path = resolve_user_path(path)
    ok, msg = _check_path(path, allowed_dirs)
    if not ok:
        return msg
    try:
        p = Path(path)
        if not p.exists():
            return f"Folder not found: {path}"
        if not p.is_dir():
            return f"Not a directory: {path}"
        shutil.rmtree(p)
        logger.info("Deleted folder: %s", path)
        return f"Deleted folder: {path}"
    except Exception as exc:
        logger.exception("Error deleting folder %s", path)
        return f"Error deleting folder: {exc}"


def move_path(source: str, destination: str, allowed_dirs: list[str]) -> str:
    """Move or rename a file/folder."""
    source = resolve_user_path(source)
    destination = resolve_user_path(destination)
    for target in (source, destination):
        ok, msg = _check_path(target, allowed_dirs)
        if not ok:
            return msg
    try:
        src = Path(source)
        if not src.exists():
            return f"Source not found: {source}"
        shutil.move(str(src), destination)
        logger.info("Moved: %s -> %s", source, destination)
        return f"Moved: {source} -> {destination}"
    except Exception as exc:
        logger.exception("Error moving %s -> %s", source, destination)
        return f"Error moving: {exc}"


def open_file(path: str, allowed_dirs: list[str]) -> str:
    """Open a file with the default application. No path restrictions."""
    path = resolve_user_path(path)
    try:
        p = Path(path)
        if not p.exists():
            return f"File not found: {path}"
        os.startfile(str(p))
        logger.info("Opened file: %s", path)
        return f"Opened: {path}"
    except Exception as exc:
        logger.exception("Error opening file %s", path)
        return f"Error opening file: {exc}"


# ------------------------------------------------------------------
# System-wide path search
# ------------------------------------------------------------------

def find_path_system_wide(name: str, max_results: int = 50) -> str:
    """Search for a file or folder by name across ALL drives on the system.

    Drives are detected dynamically at runtime.  Protected system
    directories are silently skipped.
    """
    drives = get_all_drives()
    matches: list[str] = []
    name_lower = name.lower()

    for drive in drives:
        root_path = Path(drive).resolve()
        try:
            for root, dirs, files in os.walk(root_path, topdown=True, onerror=lambda e: None):
                if is_path_blacklisted(root):
                    dirs.clear()  # prevent descending into blacklisted dirs
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
        result += f"\n\n... (showing first {max_results} results)"
    return result


# ------------------------------------------------------------------
# Temp / junk file scanner
# ------------------------------------------------------------------

def scan_temp_files(max_results: int = 200) -> str:
    """List the largest temporary / junk files from all Windows temp locations.

    Searches:
      - %TEMP%  (user temp)
      - %TMP%   (alternate user temp)
      - C:\\Windows\\Temp  (system temp)
      - ~\\AppData\\Local\\Temp
    """
    temp_dirs: list[Path] = [
        Path(os.path.expandvars("%TEMP%")).resolve(),
        Path(os.path.expandvars("%TMP%")).resolve(),
        Path(os.path.expandvars("%WINDIR%")) / "Temp",
        Path(os.path.expanduser("~")) / "AppData" / "Local" / "Temp",
    ]
    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique_dirs: list[Path] = []
    for d in temp_dirs:
        if d not in seen:
            seen.add(d)
            unique_dirs.append(d)

    files: list[tuple[int, str]] = []  # (size_bytes, str_path)
    for temp_dir in unique_dirs:
        if not temp_dir.exists():
            continue
        for root, _dirs, filenames in os.walk(
            temp_dir, topdown=True, onerror=lambda e: None
        ):
            root_p = Path(root)
            for fname in filenames:
                fp = root_p / fname
                try:
                    files.append((fp.stat().st_size, str(fp)))
                except OSError:
                    pass
            if len(files) >= max_results * 2:  # gather extra, sort, then trim
                break

    if not files:
        return "No temporary files found."

    files.sort(key=lambda x: x[0], reverse=True)
    total_bytes = sum(s for s, _ in files)
    total_mb = total_bytes / (1024 ** 2)
    lines = [f"Found {len(files)} temp files — total {total_mb:.1f} MB\n"]
    for size, path in files[:max_results]:
        if size < 1024:
            sz = f"{size} B"
        elif size < 1024 * 1024:
            sz = f"{size / 1024:.1f} KB"
        else:
            sz = f"{size / (1024 ** 2):.1f} MB"
        lines.append(f"  {sz:>10}  {path}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Installed app disk-usage scanner
# ------------------------------------------------------------------

def scan_app_sizes(max_results: int = 30) -> str:
    """Scan top-level folders inside Program Files and AppData\\Local,
    reporting total disk usage for each — useful for finding large apps.

    Searches:
      - %PROGRAMFILES%
      - %PROGRAMFILES(X86)%
      - ~\\AppData\\Local
    """
    scan_dirs: list[Path] = [
        Path(os.path.expandvars("%PROGRAMFILES%")).resolve(),
        Path(os.path.expandvars("%PROGRAMFILES(X86)%")).resolve(),
        Path(os.path.expanduser("~")) / "AppData" / "Local",
    ]

    app_sizes: list[tuple[int, str]] = []  # (total_bytes, str_path)
    for base_dir in scan_dirs:
        if not base_dir.exists():
            continue
        try:
            for entry in base_dir.iterdir():
                if not entry.is_dir():
                    continue
                total = 0
                try:
                    for root, _dirs, files in os.walk(
                        entry, onerror=lambda e: None
                    ):
                        root_p = Path(root)
                        for fname in files:
                            try:
                                total += (root_p / fname).stat().st_size
                            except OSError:
                                pass
                except (PermissionError, OSError):
                    pass
                app_sizes.append((total, str(entry)))
        except (PermissionError, OSError):
            pass

    if not app_sizes:
        return "No installed application folders found."

    app_sizes.sort(key=lambda x: x[0], reverse=True)
    lines = [f"Top {min(max_results, len(app_sizes))} app folders by disk usage:\n"]
    for size, path in app_sizes[:max_results]:
        if size < 1024 * 1024:
            sz = f"{size / 1024:.1f} KB"
        elif size < 1024 ** 3:
            sz = f"{size / (1024 ** 2):.1f} MB"
        else:
            sz = f"{size / (1024 ** 3):.1f} GB"
        lines.append(f"  {sz:>10}  {path}")
    return "\n".join(lines)