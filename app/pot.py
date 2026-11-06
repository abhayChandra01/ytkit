"""Runs the bgutil PO token server next to the app.

YouTube asks servers on flagged (datacenter) IPs for a "proof of origin" token.
The bgutil-ytdlp-pot-provider yt-dlp plugin fetches those tokens from a small
local HTTP server on 127.0.0.1:4416. The Docker image ships that server in
/opt/bgutil; this module starts it, restarts it if it dies, and stops it on exit.

Outside Docker nothing happens unless YTKIT_POT_SERVER_DIR points at a checkout.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger("ytkit.pot")

PORT = int(os.getenv("YTKIT_POT_PORT", "4416") or 4416)
SERVER_DIR = Path(os.getenv("YTKIT_POT_SERVER_DIR", "/opt/bgutil"))
ENABLED = os.getenv("YTKIT_POT_PROVIDER", "true").strip().lower() not in {"0", "false", "no", "off"}


class PotServer:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._watch: threading.Thread | None = None

    def _command(self) -> list[str] | None:
        deno = shutil.which("deno")
        main_ts = SERVER_DIR / "src" / "main.ts"
        if deno and main_ts.is_file():
            modules = str(SERVER_DIR / "node_modules")
            return [
                deno, "run", "--allow-env", "--allow-net",
                f"--allow-ffi={modules}", f"--allow-read={modules}",
                str(main_ts), "--host", "127.0.0.1", "--port", str(PORT),
            ]
        node = shutil.which("node")
        main_js = SERVER_DIR / "build" / "main.js"
        if node and main_js.is_file():
            return [node, str(main_js), "--host", "127.0.0.1", "--port", str(PORT)]
        return None

    def available(self) -> bool:
        return ENABLED and self._command() is not None

    def start(self) -> None:
        if not ENABLED:
            log.info("PO token server disabled (YTKIT_POT_PROVIDER=false)")
            return
        if self._command() is None:
            log.info("PO token server not installed (%s); skipping", SERVER_DIR)
            return
        self._spawn()
        self._watch = threading.Thread(target=self._watchdog, name="ytkit-pot", daemon=True)
        self._watch.start()

    def _spawn(self) -> None:
        cmd = self._command()
        if not cmd:
            return
        env = os.environ.copy()
        env.setdefault("DENO_DIR", str(SERVER_DIR / ".cache" / "deno"))
        env.setdefault("DENO_NO_PROMPT", "1")
        env.setdefault("DENO_NO_UPDATE_CHECK", "1")
        try:
            self.proc = subprocess.Popen(cmd, cwd=SERVER_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.info("Started PO token server (pid %s) on 127.0.0.1:%s", self.proc.pid, PORT)
        except OSError as e:
            log.error("Couldn't start PO token server: %s", e)
            self.proc = None

    def _watchdog(self) -> None:
        while not self._stop.wait(30):
            if self.proc is not None and self.proc.poll() is not None:
                log.warning("PO token server exited with %s; restarting", self.proc.returncode)
                self._spawn()

    def healthy(self) -> bool:
        if not self.proc or self.proc.poll() is not None:
            return False
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{PORT}/ping", timeout=2
            ) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def stop(self) -> None:
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


server = PotServer()
