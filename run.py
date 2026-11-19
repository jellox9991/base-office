"""Start BASE Design Office.

    python run.py              -> this computer only:      http://127.0.0.1:5000
    python run.py --phone      -> also your phone on the same Wi-Fi: http://<this PC's IP>:5000

Data (database + uploaded files) lives in .\\data unless BASE_OFFICE_DATA points elsewhere.
"""
import os
import socket
import sys
import webbrowser

from app import create_app

app = create_app()

if __name__ == "__main__":
    phone = "--phone" in sys.argv
    host = "0.0.0.0" if phone else "127.0.0.1"
    port = int(os.environ.get("BASE_OFFICE_PORT", "5000"))
    print(f"BASE Design Office - data folder: {app.config['DATA_DIR']}")
    print(f"Open http://127.0.0.1:{port}")
    if phone:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            print(f"On your phone (same Wi-Fi): http://{s.getsockname()[0]}:{port}")
            s.close()
        except OSError:
            pass
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{port}")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=4)
    except ImportError:
        app.run(host=host, port=port, debug=False)
