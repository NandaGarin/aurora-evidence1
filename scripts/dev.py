"""Run migrations then the API (and the Vite frontend when it exists).

One documented command for local development:

    uv run python scripts/dev.py

Ports come from the environment (AURORA_PORT / AURORA_FRONTEND_PORT), defaulting
to 8102 / 5172 per the shared contract so all three AURORA modules can run side
by side on one machine.

The frontend is optional: this module is usable as a pure API service, so a
missing ``frontend/`` directory is reported and skipped rather than fatal.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - .env is optional
    pass

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

HOST = os.getenv("AURORA_HOST", "127.0.0.1")
PORT = os.getenv("AURORA_PORT", "8102")
FRONTEND_PORT = os.getenv("AURORA_FRONTEND_PORT", "5172")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _run(cmd: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    # Refuse a non-local bind unless public mode is explicitly configured.
    if HOST not in LOCAL_HOSTS and os.getenv("AURORA_PUBLIC", "false").lower() != "true":
        raise SystemExit(
            "Bind di luar localhost memerlukan AURORA_PUBLIC=true dan "
            "AURORA_API_TOKEN yang kuat (>=32 karakter)."
        )

    env = {**os.environ, "PYTHONPATH": str(BACKEND)}

    # 1. Database schema first; the API assumes the tables exist.
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT)

    children: list[subprocess.Popen] = []
    try:
        # 2. API + in-process worker.
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.api.main:app",
                    "--host",
                    HOST,
                    "--port",
                    PORT,
                ],
                cwd=BACKEND,
                env=env,
            )
        )
        print(f"API      : http://{HOST}:{PORT}  (OpenAPI di /docs)", flush=True)

        # 3. Frontend, only if present.
        if (FRONTEND / "package.json").exists():
            if not (FRONTEND / "node_modules").exists():
                _run(["npm", "install"], cwd=FRONTEND)
            children.append(
                subprocess.Popen(
                    ["npm", "run", "dev", "--", "--port", FRONTEND_PORT],
                    cwd=FRONTEND,
                )
            )
            print(f"Frontend : http://{HOST}:{FRONTEND_PORT}", flush=True)
        else:
            print(
                "Frontend : belum ada (frontend/ tidak ditemukan) — API tetap jalan.",
                flush=True,
            )

        def _stop(*_):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _stop)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
