"""Start / stop the analysis batch from the app as a background process."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import config

PID_FILE = config.DB_PATH.parent / "batch.pid"
LOG_FILE = config.DB_PATH.parent / "batch.log"
_proc: Optional[subprocess.Popen] = None


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) // 2)  # roughly the physical cores


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        return False  # no cheap, safe check without extra dependencies
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.exists():  # guard against a recycled pid
        return b"chesscoach.engine" in cmdline.read_bytes()
    return True


def running_pid() -> Optional[int]:
    """PID of a batch started from the app (also found again after an app restart)."""
    global _proc
    if _proc is not None:
        if _proc.poll() is None:
            return _proc.pid
        _proc = None
    try:
        pid = int(PID_FILE.read_text())
    except (OSError, ValueError):
        return None
    if _pid_alive(pid):
        return pid
    PID_FILE.unlink(missing_ok=True)
    return None


def start(date_from: str, date_to: str, time_controls: list[str], workers: int) -> int:
    global _proc
    pid = running_pid()
    if pid:
        return pid
    config.stockfish_path()  # raises a helpful FileNotFoundError when missing
    cmd = [sys.executable, "-m", "chesscoach.engine", "--batch", "--workers", str(workers),
           "--since", str(date_from), "--until", str(date_to), "--time-controls", *time_controls]
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "w", encoding="utf-8")
    kwargs: dict = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    _proc = subprocess.Popen(cmd, cwd=config.PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env={**os.environ, "CHESSCOACH_DB": str(config.DB_PATH)},
                             **kwargs)
    PID_FILE.write_text(str(_proc.pid))
    return _proc.pid


def stop() -> bool:
    """Ask the batch to stop. Finished games are saved; the rest resumes next time."""
    pid = running_pid()
    if not pid:
        return False
    try:
        if os.name == "nt":
            (_proc.terminate() if _proc is not None else None)
        else:
            os.kill(pid, signal.SIGINT)  # same as Ctrl-C: flushes finished games first
    except OSError:
        return False
    return True


def log_tail(lines: int = 6) -> str:
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace").replace("\r", "\n")
    except OSError:
        return ""
    return "\n".join([ln for ln in text.splitlines() if ln.strip()][-lines:])
