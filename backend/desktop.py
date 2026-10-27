"""Desktop build: the FastAPI server and the game in one native window (pywebview).

    python desktop.py                 # play locally (server on 127.0.0.1, random port)
    python desktop.py --lan           # host for friends on your network (port 8000)
    python desktop.py --connect https://election.example.com   # join a remote server, no local one

The server runs in a background thread and the window is a plain pywebview browser pointed at it,
so the frontend is exactly the web build: same origin, relative URLs, nothing to configure.
Closing the window stops the server cleanly, which flushes the persistence database.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

APP_NAME = "Election Night"
FROZEN = getattr(sys, "frozen", False)
# PyInstaller unpacks data next to the `app` package (see election-night.spec); in a checkout
# this file sits in backend/, next to app/ and maps/, with frontend/ one level up.
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
FRONTEND = BUNDLE / "frontend" if FROZEN else BUNDLE.parent / "frontend"


def data_dir() -> Path:
    """Per-user folder for the room database and the WebView's localStorage (rejoin tokens)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        d = base / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "election-night"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lan_ip() -> str:
    """The address friends on the same network can reach. A UDP connect sends no packets;
    it only makes the OS pick the outgoing interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))   # TEST-NET-1: never routed, just selects a route
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def start_server(host: str, port: int):
    """Bind first (so a busy port fails loudly, before any window opens), then serve in a thread."""
    import uvicorn
    from app.server import app      # imported here: server.py reads ELECTION_* env vars at import

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as e:
        sys.exit(f"Cannot listen on {host}:{port} ({e.strerror}). Is another copy running? Try --port.")
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if not thread.is_alive() or time.time() > deadline:
            sys.exit("The game server failed to start.")
        time.sleep(0.05)
    return server, thread, port


def main() -> None:
    p = argparse.ArgumentParser(description=f"{APP_NAME} desktop app")
    p.add_argument("--lan", action="store_true", help="host for other players on your network")
    p.add_argument("--port", type=int, help="port to listen on (default: random, or 8000 with --lan)")
    p.add_argument("--connect", metavar="URL", help="open a remote server instead of starting one")
    p.add_argument("--debug", action="store_true", help="enable the WebView developer tools")
    args = p.parse_args()

    import webview   # after argparse, so --help works without a GUI toolkit installed

    data = data_dir()
    # Windowed build: no console, and uvicorn's logging would crash on sys.stdout being None.
    # A log file also keeps the printed admin token somewhere readable.
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(data / "server.log", "a", buffering=1, encoding="utf-8")

    server = thread = None
    if args.connect:
        url = args.connect.rstrip("/")
    else:
        os.environ.setdefault("ELECTION_DB", str(data / "election.db"))
        os.environ.setdefault("ELECTION_FRONTEND", str(FRONTEND))
        host = "0.0.0.0" if args.lan else "127.0.0.1"
        port = args.port if args.port is not None else (8000 if args.lan else 0)
        if args.lan and port:
            # Invite links must point at an address friends can open, not the window's 127.0.0.1.
            os.environ.setdefault("ELECTION_PUBLIC_URL", f"http://{lan_ip()}:{port}")
        sys.path.insert(0, str(BUNDLE))
        server, thread, port = start_server(host, port)
        url = f"http://127.0.0.1:{port}"   # loopback is a secure context, so the Clipboard API works
        if args.lan:
            print(f"\n  Hosting on {os.environ['ELECTION_PUBLIC_URL']}  (friends open this in a browser)\n", flush=True)

    webview.create_window(APP_NAME, url, width=1360, height=860, min_size=(380, 560), background_color="#0f1420")
    icon = FRONTEND / "icons" / "icon-512.png"
    # private_mode=False + storage_path: localStorage survives restarts, so "session:<code>"
    # still lets the player rejoin a running game after closing the app.
    webview.start(private_mode=False, storage_path=str(data / "webview"), debug=args.debug,
                  icon=str(icon) if icon.exists() else None)

    if server:
        server.should_exit = True    # runs the FastAPI lifespan exit: ticker stops, persistence flushes
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
