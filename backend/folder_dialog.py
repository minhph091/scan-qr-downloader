from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def pick_folder_native(initial_dir: Optional[str] = None) -> Optional[str]:
    """
    Open a native OS folder selection dialog in an isolated subprocess
    so tkinter never blocks or conflicts with the FastAPI/asyncio event loop.
    """
    start_dir = ""
    if initial_dir and Path(initial_dir).exists():
        start_dir = str(Path(initial_dir).resolve())

    script = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "initial = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None\n"
        "folder = filedialog.askdirectory(title='Chọn thư mục lưu file tải về', initialdir=initial)\n"
        "root.destroy()\n"
        "if folder:\n"
        "    sys.stdout.buffer.write(folder.encode('utf-8'))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script, start_dir],
            capture_output=True,
            timeout=180,
        )
        if proc.returncode == 0 and proc.stdout:
            selected = proc.stdout.decode("utf-8", errors="replace").strip()
            if selected:
                return str(Path(selected))
    except Exception:
        return None
    return None


def open_folder_in_explorer(folder_path: str) -> bool:
    """Open target directory in Windows Explorer (or platform file manager)."""
    target = Path(folder_path)
    target.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except Exception:
        return False
