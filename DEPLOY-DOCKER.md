# IT Projects – deployment guide for the infrastructure team (Docker)

This file is for the people who run the application. The application is a Django 5.2 web app
(Python 3.13) served by Waitress, using PostgreSQL. It is packaged as one container image.

## 1. What you get

| Item | Detail |
|---|---|
| Image | built from `Dockerfile` (base `python:3.13-slim-bookworm`), runs as user `app` (uid/gid 10001), **no secrets inside** |
| Process | `python serve.py` (Waitress, 8 threads) on `0.0.0.0:8000` inside the container |
| Database | **not included**: an external PostgreSQL 16 or newer (the compose file starts one only for trials) |
| Uploaded files | written to `/app/media` → **mount a persistent volume there** |
| Static files | built into the image (`collectstatic`) and served by WhiteNoise: no separate web server needed |
| HTTPS | **not handled by the container**: terminate TLS in your reverse proxy / load balancer |
| Logs | standard output / error of the container |
| Health | `GET /healthz/` → `200 ok` when the database answers, `503` otherwise (no login, no data). The image already has a `HEALTHCHECK` |

Build: `docker build -t itpm:<version> .`

## 2. Settings (environment variables)

| Variable | Required | Meaning |
|---|---|---|
| `SECRET_KEY` | yes | long random secret (changing it logs everybody out). `python -c "import secrets; print(secrets.token_urlsafe(60))"` |
| `ALLOWED_HOSTS` | yes | public host name(s), comma separated, e.g. `itpm.company.mr`. Any other `Host` header gets 400 |
| `DB_NAME` `DB_USER` `DB_PASSWORD` `DB_HOST` `DB_PORT` | yes | PostgreSQL connection (port default 5432). The database user needs rights to create tables (migrations) |
| `HTTPS` | yes in production | `True` when users reach the site with `https://` (redirect to https, Secure cookies, HSTS) |
| `HSTS_SECONDS` | no | default 3600. Raise to `31536000` after a quiet week |
| `SITE_URL` | yes | public address with `https://`, used for links in emails |
| `TRUSTED_PROXY` | **yes when `HTTPS=True`** | address of your reverse proxy, or `*` if only the proxy can reach the container. See section 3 |
| `DEBUG` | no | must stay `False` (the image sets it) |
| `RUN_MIGRATIONS` | no | default `1`: the container applies database migrations at start. Set `0` if you run them as a separate job |
| `SERVE_HOST` / `SERVE_PORT` | no | default `0.0.0.0` / `8000` |
| `EMAIL_BACKEND` and friends | no | **see section 6: real email is not built yet** |

`docker.env.example` lists all of them. Do not put real values in git.

## 3. Reverse proxy (important)

The proxy must:
- terminate HTTPS and forward to the container's port 8000;
- send `Host: <your public name>`, `X-Forwarded-Proto: https` and `X-Forwarded-For: <visitor address>`;
- allow request bodies of at least 20 MB (uploads are limited to 10 MB by the app).

The app only believes `X-Forwarded-*` headers from the address(es) in `TRUSTED_PROXY`.
If `HTTPS=True` and `TRUSTED_PROXY` is wrong, every page redirects to https forever (redirect loop),
and the login lockout (django-axes) would see the proxy's address instead of the visitor's.
`/healthz/` is exempt from the https redirect so the container's own check works.

## 4. First start and administration

1. Start the container with a reachable, empty database: it waits for the database (up to 60 s), applies migrations, then serves.
2. Create the first administrator (interactive): `docker exec -it <container> python manage.py createsuperuser`.
   After that, the administrator creates all other accounts in the application (Administration section).
3. Daily reminder emails (when email exists, section 6): schedule `python manage.py send_reminders` once a day
   with the same image and environment (`docker run ... itpm:<version> python manage.py send_reminders`, or a cron / Kubernetes job).
   Any command other than `python serve.py` skips the database wait and migrations.

## 5. Data and backups – your responsibility

Two things hold data and **both** must be backed up together: the PostgreSQL database (`pg_dump`) and the `/app/media` volume.
The image itself holds nothing to back up. People are deactivated, never deleted; a restore test is recommended before real use.
Several replicas are possible (sessions are in the database) if they share the same `/app/media` volume;
run migrations from one place only (`RUN_MIGRATIONS=0` on the others).

## 6. Known limits (honest list)

- **Email is not finished (planned "Step 15d")**: with the default `console` backend, emails are only printed in the logs.
  The application needs Microsoft 365 (Graph) sending to be built; tell the developer which mail system you will provide
  (SMTP relay or Microsoft 365) – SMTP settings already work through `EMAIL_HOST*` variables.
- No two-factor login, no password reset by email (an administrator sets a new password).
- Time zone is fixed to `Africa/Nouakchott`; the interface has English, French and Arabic.

## 7. Update procedure

Build the new image, stop the old container, start the new one with the same settings and volumes
(migrations run automatically). Keep the previous image tag to roll back; migrations are designed to be
backward-compatible, but take a database backup before every update.

## 8. What was verified, and what was not

Verified by the developer with the real `Dockerfile` (but on a stand-in Ubuntu 24.04 / Python 3.12 base,
because the official Python image could not be downloaded from the build workspace): image builds;
`collectstatic` (147 files); container starts on an empty PostgreSQL, applies all migrations, runs as uid 10001,
reports healthy; plain http → 301; request with proxy headers → 200; unknown Host → 400; static files served;
private pages and uploads require login; media volume writable; waits and retries when the database is unreachable.
**Not verified:** the build on the official `python:3.13-slim-bookworm` base (please run `docker build` once and report any error),
`docker compose up` with the `postgres:16` image, your proxy, your orchestrator.
