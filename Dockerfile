# IT Projects - container image (Django + Waitress). See DEPLOY-DOCKER.md.
# Build:  docker build -t itpm:latest .
# The image holds the code only. Settings come from environment variables, the database is a separate
# PostgreSQL, and uploaded files go to the volume /app/media.
ARG BASE_IMAGE=python:3.13-slim-bookworm
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata: the app uses the time zone Africa/Nouakchott. A fixed non-root user runs the app.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Packages first: this layer is rebuilt only when requirements.txt changes
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Gather the CSS/JavaScript/fonts now (the settings need these variables to load; the values are not used).
# media/ is where uploaded files are written: mount a volume on it.
RUN SECRET_KEY=build-only DB_NAME=build DB_USER=build DB_PASSWORD=build DEBUG=False \
        python manage.py collectstatic --noinput \
 && mkdir -p /app/media \
 && chown -R app:app /app/media /app/staticfiles \
 && sed -i 's/\r$//' /app/docker/entrypoint.sh \
 && chmod +x /app/docker/entrypoint.sh   # Windows line endings would break the script

USER app

# Listen on all addresses of the container (the default, 127.0.0.1, would be unreachable from outside)
ENV DEBUG=False \
    SERVE_HOST=0.0.0.0 \
    SERVE_PORT=8000

EXPOSE 8000
VOLUME ["/app/media"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "/app/docker/healthcheck.py"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "serve.py"]
