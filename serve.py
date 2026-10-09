"""Runs the app with Waitress, a web server made for real use that works on Windows.

    python serve.py

It answers on http://127.0.0.1:8000, for this computer only. On the server (Step 15b),
Caddy receives the visitors on https:// and passes their requests to it.
The app's settings come from .env, as always.

"python manage.py runserver" is only for developing: it is slower and less safe.
"""

from waitress import serve

from config.wsgi import application

print("IT Projects is running on http://127.0.0.1:8000 (press Ctrl+C to stop)", flush=True)
serve(
    application,
    listen="127.0.0.1:8000",
    threads=8,  # how many requests it handles at the same time
    # Caddy, on this same computer, tells the app the visitor's real address
    # and that they used https. Waitress only believes this from 127.0.0.1.
    trusted_proxy="127.0.0.1",
    trusted_proxy_count=1,
    trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
    # Refuse requests bigger than 20 MB (uploaded files are limited to 10 MB anyway)
    max_request_body_size=20 * 1024 * 1024,
)
