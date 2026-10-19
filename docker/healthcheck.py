"""Used by Docker's HEALTHCHECK: asks the app on /healthz/ (from inside the container).

Exit code 0 = healthy, 1 = not healthy. The request carries a Host name the app accepts
(the first name of ALLOWED_HOSTS), otherwise Django would refuse it.
"""

import os
import sys
import urllib.request

hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip() and h.strip() != "*"]
host = hosts[0].lstrip(".") if hosts else "localhost"
port = os.environ.get("SERVE_PORT", "8000")

request = urllib.request.Request(f"http://127.0.0.1:{port}/healthz/", headers={"Host": host})
try:
    with urllib.request.urlopen(request, timeout=4) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception as error:
    print(f"unhealthy: {error}", file=sys.stderr)
    sys.exit(1)
