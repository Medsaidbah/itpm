IT Projects (Mchariee) - test / interim server pack, version 1.0.1
===================================================================

Files in this pack
  mchariee-1.0.1.tar      the application image (Docker)
  docker-compose.yml      the app + its PostgreSQL 16 database (database runs as a second container)
  docker.env.example      the settings to copy to docker.env and fill in
  DEPLOY-DOCKER.md, EMAIL-M365-SETUP.md   guides
The database content is a separate file (mchariee-manager-data.zip), sent privately.
Nothing in this pack contains a password.

What you need: a server with Docker and Docker Compose, ~1 GB free RAM, a few GB of disk.

1. Load the image
     docker load -i mchariee-1.0.1.tar

2. Create the settings file
     copy docker.env.example docker.env      (Windows)   /   cp docker.env.example docker.env   (Linux)
   Edit docker.env:
     SECRET_KEY=<a long random value>
     DB_PASSWORD=<a strong password; used only between the two containers>
     ALLOWED_HOSTS=<the name or IP people type in the browser>
     SITE_URL=http://<same name or IP>:8000        (it is used for the links inside emails)
     BIND_ADDRESS=0.0.0.0                          (so other computers can reach port 8000)
     HTTPS=False                                   (True only behind a proxy with a certificate)
   Email (Microsoft 365 SMTP). You will receive the mailbox password separately:
     EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
     EMAIL_HOST=smtp.office365.com
     EMAIL_PORT=587
     EMAIL_USE_TLS=True
     EMAIL_HOST_USER=ddi-notifications@mtnima.gov.mr
     EMAIL_HOST_PASSWORD=<given separately>
     DEFAULT_FROM_EMAIL=ddi-notifications@mtnima.gov.mr
   Keep the three DJANGO_SUPERUSER_* lines commented (with #): the accounts come with the data below.
   Each setting name must appear only once in the file.

3. Start the database only, then load the data
     docker compose --env-file docker.env up -d db
     (wait 15 seconds)
     docker compose --env-file docker.env cp mchariee-manager-data.sql db:/tmp/data.sql
     docker compose --env-file docker.env exec -T db psql -U itpm_user -d itpm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
     docker compose --env-file docker.env exec -T db psql -U itpm_user -d itpm -v ON_ERROR_STOP=1 -f /tmp/data.sql
   (use your DB_USER and DB_NAME if you changed them in docker.env)

4. Start the application
     docker compose --env-file docker.env up -d
     Check: http://<server>:8000/healthz/  must answer "ok". Then log in with an existing account.
     If someone forgot a password: docker compose --env-file docker.env exec web python manage.py changepassword <username>

5. Two scheduled jobs (cron or Windows Task Scheduler), from the folder of docker-compose.yml:
     every day at 07:00 : docker compose --env-file docker.env --profile jobs run --rm reminders
     every 10 minutes   : docker compose --env-file docker.env --profile jobs run --rm outbox

6. Backups (please keep them): every night
     docker compose --env-file docker.env exec -T db pg_dump -U itpm_user -d itpm --no-owner --no-privileges -f /tmp/backup.sql
     docker compose --env-file docker.env cp db:/tmp/backup.sql <backup folder>/itpm-<date>.sql
   and the Docker volume itpm_media (uploaded files). NEVER run "docker compose down -v": it deletes the data.

7. Logs: docker compose --env-file docker.env logs web --tail 50
