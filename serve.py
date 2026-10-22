"""Runs the app with Waitress, a web server made for real use that works on Windows.

    python serve.py

It answers on http://127.0.0.1:8000, for this computer only. On the server (Step 15b),
Caddy receives the visitors on https:// and passes their requests to it.
The app's settings come from .env, as always.

Inside a Docker container (see DEPLOY-DOCKER.md) three optional variables change where it listens:
SERVE_HOST (default 127.0.0.1), SERVE_PORT (default 8000) and TRUSTED_PROXY (default 127.0.0.1:
the address of the reverse proxy that is allowed to say "this visitor used https"). Without them
nothing changes.

"python manage.py runserver" is only for developing: it is slower and less safe.
"""

import os

from waitress import serve

from config.wsgi import application

HOST = os.environ.get("SERVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("SERVE_PORT", "8000"))
TRUSTED_PROXY = os.environ.get("TRUSTED_PROXY", "127.0.0.1")

print(f"مشاريعي is running on http://{HOST}:{PORT} (press Ctrl+C to stop)", flush=True)
serve(
    application,
    listen=f"{HOST}:{PORT}",
    threads=8,  # how many requests it handles at the same time
    # Caddy, on this same computer, tells the app the visitor's real address
    # and that they used https. Waitress only believes this from 127.0.0.1.
    trusted_proxy=TRUSTED_PROXY,
    trusted_proxy_count=1,
    trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
    # Refuse requests bigger than 20 MB (uploaded files are limited to 10 MB anyway)
    max_request_body_size=20 * 1024 * 1024,
)
