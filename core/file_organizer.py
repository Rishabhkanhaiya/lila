"""
file_organizer.py — Autonomous Desktop & Downloads Workspace Organizer
========================================================================
Autonomously declutters and categorizes loose files in Downloads, Desktop,
or custom workspaces into semantic folders with collision safety and error handling.
"""

import os
import shutil
from typing import Dict, List, Any, Optional

from core.jarvis_logger import log_info, log_warn, log_error

# Semantic file extension mappings
EXT_CATEGORY_MAP = {
    # Documents
    ".pdf": "Documents", ".docx": "Documents", ".doc": "Documents",
    ".txt": "Documents", ".epub": "Documents", ".odt": "Documents",
    ".rtf": "Documents", ".md": "Documents",

    # Presentations
    ".pptx": "Presentations", ".ppt": "Presentations", ".key": "Presentations",

    # Spreadsheets
    ".xlsx": "Spreadsheets", ".xls": "Spreadsheets", ".csv": "Spreadsheets",

    # Images
    ".png": "Images", ".jpg": "Images", ".jpeg": "Images",
    ".gif": "Images", ".webp": "Images", ".svg": "Images",
    ".ico": "Images", ".bmp": "Images", ".tiff": "Images",

    # Videos
    ".mp4": "Videos", ".mkv": "Videos", ".mov": "Videos",
    ".avi": "Videos", ".webm": "Videos", ".flv": "Videos",

    # Audio
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio",
    ".aac": "Audio", ".m4a": "Audio", ".ogg": "Audio",

    # Installers & Binaries
    ".exe": "Installers", ".msi": "Installers", ".iso": "Installers",

    # Archives
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
    ".tar": "Archives", ".gz": "Archives",

    # Code
    ".py": "Code", ".js": "Code", ".ts": "Code", ".html": "Code",
    ".css": "Code", ".json": "Code", ".cpp": "Code", ".c": "Code",
    ".h": "Code", ".java": "Code", ".rs": "Code", ".go": "Code"
}


def organize_folder(folder_path: Optional[str] = None) -> str:
    """
    Organizes files in the target directory into structured category subfolders.
    Defaults to user's Downloads folder if folder_path is None or empty.
    """
    if not folder_path:
        target_dir = os.path.expanduser("~/Downloads")
    else:
        # Support aliases
        clean = folder_path.strip().lower()
        if clean in ["downloads", "download"]:
            target_dir = os.path.expanduser("~/Downloads")
        elif clean in ["desktop"]:
            target_dir = os.path.expanduser("~/Desktop")
        elif clean in ["documents", "docs"]:
            target_dir = os.path.expanduser("~/Documents")
        else:
            target_dir = os.path.abspath(folder_path)

    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        return f"Directory '{target_dir}' does not exist or is not a folder."

    log_info("file_organizer", f"Organizing directory: {target_dir}")

    counts: Dict[str, int] = {}
    skipped = 0

    try:
        entries = os.listdir(target_dir)
    except Exception as e:
        return f"Failed to list contents of '{target_dir}': {e}"

    for item_name in entries:
        item_path = os.path.join(target_dir, item_name)

        # Safety: Ignore directories and hidden/system files
        if os.path.isdir(item_path):
            continue
        if item_name.startswith(".") or item_name.startswith("~$"):
            continue

        # Get extension
        _, ext = os.path.splitext(item_name)
        ext_lower = ext.lower()
        category = EXT_CATEGORY_MAP.get(ext_lower, "Others")

        cat_folder = os.path.join(target_dir, category)
        try:
            os.makedirs(cat_folder, exist_ok=True)
        except Exception as me:
            log_warn(f"[file_organizer]: Could not create folder '{cat_folder}': {me}")
            continue

        dest_path = os.path.join(cat_folder, item_name)

        # Collision resolution
        if os.path.exists(dest_path):
            base, ext_part = os.path.splitext(item_name)
            counter = 1
            while os.path.exists(os.path.join(cat_folder, f"{base} ({counter}){ext_part}")):
                counter += 1
            dest_path = os.path.join(cat_folder, f"{base} ({counter}){ext_part}")

        try:
            shutil.move(item_path, dest_path)
            counts[category] = counts.get(category, 0) + 1
        except Exception as e:
            log_warn(f"[file_organizer]: Skipping locked/in-use file '{item_name}': {e}")
            skipped += 1

    total_moved = sum(counts.values())
    if total_moved == 0:
        return f"Folder '{os.path.basename(target_dir)}' is already completely organized (no loose files to sort)."

    summary_parts = [f"{cat}: {cnt}" for cat, cnt in sorted(counts.items())]
    summary_str = ", ".join(summary_parts)
    return (
        f"Successfully organized {total_moved} files in '{os.path.basename(target_dir)}' into categories: "
        f"{summary_str}." + (f" (Skipped {skipped} in-use files)" if skipped else "")
    )
