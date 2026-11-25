"""Seed a temporary demo, then supervise its two foreground processes."""

import os
import signal
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from deploy.render_demo import prepare_state, validate_settings


def main():
    if os.environ.get("ECOKITCHEN_PUBLIC_DEMO") != "1":
        raise SystemExit("ECOKITCHEN_PUBLIC_DEMO=1 is required for the dedicated demo image.")
    if os.environ.get("GEMINI_API_KEY", "").strip():
        raise SystemExit("Remove the Gemini key from the public demo.")
    origin = os.environ.get("RENDER_EXTERNAL_URL", "")
    try:
        validate_settings(origin, os.environ.get("ECOKITCHEN_SECRET_KEY", ""))
        port = int(os.environ.get("PORT", "10000"))
        if not 1024 <= port <= 65535 or port in {8000, 18012, 18013, 19099}:
            raise ValueError("PORT must be an available, non-reserved port above 1023.")
    except ValueError as error:
        raise SystemExit(str(error)) from None

    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)

    os.umask(0o077)
    with TemporaryDirectory(prefix="ecokitchen-render-") as state:
        directory = Path(state)
        prepare_state(directory)
        config = Path(__file__).with_name("nginx.render.conf").read_text(encoding="utf-8")
        config_path = directory / "nginx.conf"
        config_path.write_text(
            config.replace("@PORT@", str(port)).replace("@HOST@", origin.removeprefix("https://")),
            encoding="utf-8",
        )
        if stopping:
            return 0
        env = {**os.environ, "ECOKITCHEN_DEMO_STATE": state}
        env.pop("GUNICORN_CMD_ARGS", None)
        children = []
        try:
            children.append(subprocess.Popen([
                "gunicorn", "--config", str(Path(__file__).with_name("gunicorn.conf.py")),
                "--bind", "127.0.0.1:8000", "--workers", "1", "--timeout", "30",
                "deploy.render_demo:application()",
            ], env=env))
            # Nginx serves public assets and is the only process binding the assigned port.
            # The app owns the runtime secret. Nginx does not receive it.
            proxy_env = {key: value for key, value in env.items() if key != "ECOKITCHEN_SECRET_KEY"}
            children.append(subprocess.Popen(["nginx", "-p", state + "/", "-c", str(config_path)], env=proxy_env))
            while not stopping:
                if any(child.poll() is not None for child in children):
                    return 1
                time.sleep(0.2)
            return 0
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            deadline = time.monotonic() + 8
            for child in children:
                try:
                    child.wait(timeout=max(deadline - time.monotonic(), 0))
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
