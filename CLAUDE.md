# Mchariee (itpm)

Django 5.2 web app (Python 3.13), served by Waitress, PostgreSQL database, shipped as one Docker image.
Deployment details for the infrastructure team are in DEPLOY-DOCKER.md.

## Rules

- Never run `docker compose down -v`. It deletes the database and media volumes (`itpm_db`, `itpm_media`).
- Always pass `--env-file docker.env` to `docker compose`.
- Run `manage.py test` before every commit, and only commit when it passes.
- Take a database dump before any upgrade of the live app.
- Never test restores in the project folder. Use a separate folder outside the repo.
- Keep `.env` and `docker.env` out of git. They hold secrets and are listed in `.gitignore`.
- The version appears in four places, and all four must be bumped together:
  - `docker-compose.yml` (image tag `itpm:<version>`, used three times)
  - `delivery/manifest.yml` (registry image tag)
  - `delivery/make-manager-pack.ps1` (image tag and tar file names)
  - `delivery/MANAGER-README.txt` (header and tar file name)
- The live app runs version 1.0.2, and `master` must match it.
- New work goes on the `v1.1` branch.

## Roadmap

The approved release plan and the product owner's decisions are in `docs/ROADMAP.md`. Follow it:
- 1.1 has no database change. Each later release has exactly one database change, and its migration is
  additive: defaults for old rows, never deleting or rewriting existing data.
- One feature per commit, with tests, the full suite passing, and en/fr/ar translations (including
  right to left). Stop and report after each feature.
- Ask before any change to how people use the app today.
- Mark a feature "Done" in `docs/ROADMAP.md` once it is committed.

## Migration rehearsal

Before sending a release that has a migration, rehearse it on a copy of the live database (a plain SQL `pg_dump`,
kept outside the repository):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\rehearse-migrations.ps1 -DumpPath C:\projects\live-copy\backup_itpm.sql
```

It builds the branch as `itpm-rehearsal:local`, restores the dump with psql into a separate stack
(`-p itpm-rehearsal`, its own volumes, port 8001, emails only in the log), applies the migrations, and checks
`migrate --check`, the system check, the row counts before and after, `/healthz/`, the login page and the main pages.
The report goes to `%TEMP%\itpm-rehearsal` and holds counts and status codes only.

- Never copy a dump into the repository (`*.sql` and other dump files are ignored by Git and Docker).
- The user runs the restore. Never delete the rehearsal volumes (or any volume) for them: each run empties the
  rehearsal database itself, so no `down -v` is ever needed.
- The rehearsal has no uploaded files: "File missing" on attachments is expected there.

## Running the tests

The tests use Django's built-in runner (not pytest) and need a running PostgreSQL.
The connection comes from `DB_NAME`, `DB_USER`, `DB_PASSWORD` and `DB_HOST` in the environment or `.env`.
The database user must be allowed to create databases, because Django creates a separate test database.

```powershell
.venv\Scripts\python.exe manage.py test --noinput
# one app or module:
.venv\Scripts\python.exe manage.py test projects.tests.test_permissions
```

Run the full suite and expect it to pass (it takes a little over a minute).
`OSError: down` tracebacks and "Email ... failed" lines in the output come from the email tests, which make sending fail on purpose. They are not failures.

## Building the image

```powershell
docker build -t itpm:<version> .
# or build and start with the trial PostgreSQL:
docker compose --env-file docker.env up --build
```

`delivery/make-manager-pack.ps1` saves the built image to a tar file and zips it with the docs for the manager pack.
