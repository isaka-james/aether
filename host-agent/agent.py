#!/usr/bin/env python3
"""Aether host agent.

A tiny, dependency-free HTTP service that runs natively in the user's desktop session.
It is the *only* component allowed to touch the host: the Dockerized backend sends
it validated skills to execute and synthesized speech to play. Authenticated with a
shared token (X-Aether-Token).

Run it inside your graphical session (so it inherits DISPLAY / DBUS / audio):
    AETHER_HOST_AGENT_TOKEN=... python3 agent.py
or install the provided systemd --user unit (see README.md).
"""
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import audio
import notify_recorder
import skills
from config import HOST, PORT, TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s aether-agent %(levelname)s %(message)s")
log = logging.getLogger("aether-agent")

MAX_BODY = 25 * 1024 * 1024  # 25 MB cap (audio uploads)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quieter default logging
        pass

    def _send(self, code, payload, ctype="application/json"):
        body = json.dumps(payload).encode() if ctype == "application/json" else payload
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        return self.headers.get("X-Aether-Token") == TOKEN

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            return b""
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "service": "aether-host-agent"})
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"ok": False, "error": "unauthorized"})

        if self.path == "/execute":
            try:
                req = json.loads(self._read_body() or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"ok": False, "summary": "bad request"})
            skill = req.get("skill", "")
            params = req.get("params", {})
            log.info("execute %s %s", skill, {k: ("***" if "password" in k else v)
                                              for k, v in params.items()})
            result = skills.execute(skill, params)
            return self._send(200, result)

        if self.path == "/play":
            wav = self._read_body()
            ok = audio.play_wav(wav)
            log.info("play %d bytes -> %s", len(wav), ok)
            return self._send(200, {"ok": ok})

        self._send(404, {"ok": False, "error": "not found"})


def main():
    if TOKEN == "please-change-this-shared-secret":
        log.warning("Using the default shared token! Set AETHER_HOST_AGENT_TOKEN.")
    if notify_recorder.start():
        log.info("Notification recorder watching the session bus.")
    else:
        log.info("Notification recorder off (no dbus-monitor / session bus).")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("Aether host agent listening on %s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
