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
| `EMAIL_BACKEND` | no | default: `console` (emails only appear in the log). For real email use `config.graph_email.GraphEmailBackend` (Microsoft 365) |
| `GRAPH_TENANT_ID` `GRAPH_CLIENT_ID` `GRAPH_CLIENT_SECRET` `GRAPH_SENDER` | with the Graph backend | Microsoft 365 sending; the tenant administrator follows **`EMAIL-M365-SETUP.md`** and gives you these four values. The secret goes through your secret store |

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
   On a host with no terminal (a free cloud trial), set `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD` instead: the container creates the account at its first start (then remove the password variable).
   After that, the administrator creates all other accounts in the application (Administration > Users > Import people:
   a list of first name, last name, work email; or one by one). People log in with their **work email address** and a random
   temporary password (valid 7 days) that they must replace at their first login. No email server is needed for this.
3. Email is optional at first: until it is configured, "new task" emails are only written to the container log.
   To check it later:  `docker exec -it <container> python manage.py send_test_email someone@company.mr`.
4a. Emails that failed (mail server down...) are written in an outbox and tried again by the app whenever another email is sent.
   Also schedule `python manage.py send_outbox` every 10 minutes (same image and environment) so nothing waits for the next email;
   `python manage.py send_outbox --status` shows how many wait, failed or were sent. Admins see the same in Administration > Emails.
4. Daily reminder emails: schedule `python manage.py send_reminders` once a day
   with the same image and environment (`docker run ... itpm:<version> python manage.py send_reminders`, or a cron / Kubernetes job).
   Any command other than `python serve.py` skips the database wait and migrations.

## 5. Data and backups – your responsibility

Two things hold data and **both** must be backed up together: the PostgreSQL database (`pg_dump`) and the `/app/media` volume.
The image itself holds nothing to back up. People are deactivated, never deleted; a restore test is recommended before real use.
Several replicas are possible (sessions are in the database) if they share the same `/app/media` volume;
run migrations from one place only (`RUN_MIGRATIONS=0` on the others).

## 6. Known limits (honest list)

- **Email (Step 15d)**: Microsoft 365 sending is built (`config.graph_email.GraphEmailBackend`) and tested against a simulated Microsoft service,
  **not yet against a real tenant**. Until the four `GRAPH_*` values are set and `EMAIL_BACKEND` is changed, emails are only printed in the log.
  The reminder job sends one email after the other (Exchange Online allows about 30 per minute per mailbox).
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
