"""A tiny address for the machines that watch the app (Docker, a load balancer, monitoring).

/healthz/ answers "ok" (200) when the app can reach its database, and 503 when it can't.
It shows no data and needs no login. Nothing else in the app uses it.
"""

import logging

from django.db import connection
from django.http import HttpResponse

logger = logging.getLogger(__name__)


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # any database problem means "not healthy"
        logger.exception("healthz: the database does not answer")
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")
