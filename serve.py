#!/usr/bin/env python3
"""
Demo-day launcher for the Reunification Engine.

Binds the review interface to every interface on this machine so that judges on
the same network can open it from their own phones and laptops, prints the
address in a form that is readable from across a room, and renders a QR code so
nobody has to type an IP address.

This is a LOCAL NETWORK demo server. It is not exposed to the public internet,
it holds only synthetic data, and it keeps no state beyond an in-memory triage
log that is discarded when the process stops.

Usage
-----
    python serve.py                 # bind the LAN, port 8000
    python serve.py --port 9000
    python serve.py --local-only    # 127.0.0.1 only, nothing reachable off-box
    python serve.py --no-qr
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REQUIRED = ("registry_a.json", "registry_b.json", "ground_truth.json")


def lan_address() -> str | None:
    """
    Best guess at this machine's address on the local network.

    Opens a UDP socket toward a documentation address and asks the OS which
    local interface it would route through. Nothing is actually sent, and it
    works without DNS or internet access -- which matters at a venue.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 80))  # TEST-NET-1, never routed
        addr = s.getsockname()[0]
        return None if addr.startswith("127.") else addr
    except OSError:
        return None
    finally:
        s.close()


def ensure_data() -> bool:
    """Generate the synthetic corpus if it is not already there."""
    missing = [f for f in REQUIRED if not (DATA_DIR / f).exists()]
    if not missing:
        return True
    print(f"  Synthetic corpus not found ({', '.join(missing)}).")
    print("  Generating it now…\n")
    # The child writes straight to the file descriptor, so flush first or its
    # output overtakes this notice whenever stdout is redirected.
    sys.stdout.flush()
    result = subprocess.run(
        [sys.executable, "-m", "engine.generate"], cwd=ROOT)
    print()
    return result.returncode == 0


def render_qr(url: str) -> None:
    """Print a scannable QR code in the terminal, if the library is present."""
    try:
        import qrcode
    except ImportError:
        print("  (install `qrcode` to show a scannable code here)\n")
        return
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def banner(port: int, lan: str | None, local_only: bool, show_qr: bool) -> None:
    line = "═" * 66
    print(f"\n╔{line}╗")
    print("║  REUNIFICATION ENGINE — local demo server" + " " * 24 + "║")
    print("║  Synthetic data · decision support · not a production system"
          + " " * 5 + "║")
    print(f"╚{line}╝\n")

    print(f"  On this machine     http://127.0.0.1:{port}")
    if local_only:
        print("\n  Local-only mode: nothing outside this machine can reach it.\n")
        sys.stdout.flush()
        return
    if lan:
        url = f"http://{lan}:{port}"
        print(f"  On this network     {url}")
        print("\n  Anyone on the same Wi-Fi can open that address. Nothing is")
        print("  exposed to the public internet.\n")
        if show_qr:
            render_qr(url)
            print(f"  {url}\n")
    else:
        print("\n  No local network address found — this machine may be offline.")
        print("  The 127.0.0.1 address above still works on this machine.\n")

    print("  macOS may ask whether to allow incoming connections. Allow it, or")
    print("  the phones in the room will not be able to reach the server.\n")
    print("  Ctrl-C to stop.\n")
    # uvicorn blocks immediately after this, so flush explicitly: when stdout is
    # redirected to a file or pipe it is block-buffered, and the address would
    # otherwise sit unseen in the buffer for the whole life of the server.
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the local demo server.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--local-only", action="store_true",
                    help="bind 127.0.0.1 only; nothing reachable off this machine")
    ap.add_argument("--no-qr", action="store_true", help="skip the QR code")
    ap.add_argument("--reload", action="store_true",
                    help="restart on code changes (development, not demo day)")
    args = ap.parse_args()

    if not ensure_data():
        print("  Could not generate the synthetic corpus. Aborting.")
        return 1

    lan = None if args.local_only else lan_address()
    banner(args.port, lan, args.local_only, not args.no_qr)

    try:
        import uvicorn
    except ImportError:
        print("  uvicorn is not installed. Run:  pip install -r requirements.txt")
        return 1

    try:
        uvicorn.run(
            "web.app:app",
            host="127.0.0.1" if args.local_only else "0.0.0.0",
            port=args.port,
            reload=args.reload,
            log_level="warning",
        )
    except KeyboardInterrupt:
        pass
    print("\n  Server stopped. The in-memory review log has been discarded.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
