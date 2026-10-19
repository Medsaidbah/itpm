#!/bin/sh
# Starts the app in the container:
#   1. waits until the database answers (up to 60 seconds),
#   2. applies the database migrations (unless RUN_MIGRATIONS=0),
#   3. starts the command (by default: python serve.py).
# Only the normal start does 1 and 2: "docker run ... python manage.py <something>" goes straight to the command.
set -e

if [ "$1" = "python" ] && [ "$2" = "serve.py" ]; then
  python - <<'PY'
import os, sys, time
sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.db import connection
for attempt in range(1, 31):
    try:
        connection.ensure_connection()
        break
    except Exception as error:
        print(f"waiting for the database ({attempt}/30): {error.__class__.__name__}", flush=True)
        time.sleep(2)
else:
    print("The database does not answer. Check DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD.", file=sys.stderr)
    sys.exit(1)
PY
  if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
  fi
fi

exec "$@"
