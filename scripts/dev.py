"""Run the local API and Vite together; Ctrl+C stops both child processes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    node = shutil.which("node")
    vite = ROOT / "apps" / "web" / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not vite.is_file():
        raise SystemExit("Install frontend dependencies first: cd apps/web && pnpm install")
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment.setdefault("PYTHONUTF8", "1")
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    processes: list[subprocess.Popen] = []
    try:
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                cwd=ROOT, env=environment, creationflags=creation_flags,
            )
        )
        processes.append(
            subprocess.Popen(
                [node, str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
                cwd=ROOT / "apps" / "web", env=environment, creationflags=creation_flags,
            )
        )
        print("ScholarTrace: http://127.0.0.1:5173", flush=True)
        print("API docs:     http://127.0.0.1:8000/docs", flush=True)
        print("Press Ctrl+C to stop both services.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.3)
        if any(process.poll() not in (None, 0) for process in processes):
            raise SystemExit("A service stopped. Check the error above and whether ports 8000/5173 are already in use.")
    except KeyboardInterrupt:
        print("\nStopping ScholarTrace...", flush=True)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
