# Mchariee – changelog

## 1.2.0 (2026-11-24)

### What changes for the people who use Mchariee

- **A read-only "Executive" role.** An admin can give it in Administration > Users. An executive sees every project,
  task, team, person page, contribution figure and activity, and can open every report and export, but changes
  nothing: no edits, no status changes, no comments, no files. They are never a manager or a team member and are
  never given tasks.
- **People overview** moved out of Administration to its own page (`/people/overview/`), in the menu of admins and
  executives. The old address still leads there.

### What MTNIMA needs to know

- **No new settings.**
- **One database migration, `accounts/0008`.** It only adds the new role to the list of choices of a user's role:
  it sends no SQL to PostgreSQL (`sqlmigrate` shows "no-op") and changes no data; every account keeps its role.
  It applies by itself when the new version starts, and it was rehearsed on a copy of the live database.
- **How to update.**
  1. Make a backup first: the PostgreSQL database (`pg_dump`) and the `itpm_media` volume.
  2. Keep the 1.1.0 image (do not delete it) so that you can go back to it.
  3. Start the 1.2.0 image with the same `docker.env` and the same volumes.
- **Going back to 1.1.0** works on the same database, but first give every executive another role (Administration >
  Users): 1.1.0 doesn't know the "Executive" role, so an executive would see nothing there.
- **Configuration files.** Only the image tag changed in `docker-compose.yml` and `delivery/manifest.yml` (now
  `registry-mtnima.gov.mr/ddirepo/mchariee:1.2.0`).

## 1.1.0 (prepared on 2026-11-18)

### What changes for the people who use Mchariee

- **Clickable links.** Web addresses written in comments and in task or project descriptions can be clicked; they open in a new tab.
- **Teams in the project list.** The project list shows each project's team as small pictures, so you see who works on a project before opening it.
- **Behind schedule.** A project's page compares its progress with the progress expected by today and says when it is more than 10 points behind.
- **Late projects stand out.** In the project list and on the Dashboard, late projects are marked in red and projects behind schedule in orange, always with a word, not only a colour.
- **Projects at a glance.** The home page counts your projects by status (planned, in progress, on hold, completed) and shows how many are late, behind schedule or have a blocked task. Each number opens the matching list. The Dashboard also counts the projects behind schedule.
- **File previews.** PDFs and pictures (PNG, JPEG, GIF, WebP) attached to a task can be looked at without downloading them: pictures show as thumbnails, PDFs open in the browser's viewer. Other files are downloaded as before.
- **Contribution summary.** A person's page shows their work in figures (tasks done, open and overdue tasks, tasks done on time, comments, files, last activity). Only the person, the admins, and their project managers (limited to their own projects) see these figures. Admins also get a "People overview" in Administration.
- **A reason for blocking.** Setting a task to "Blocked" now asks why. The reason is added to the task's comments with the name of the person who blocked it. Tasks that were already blocked are not affected.

### What MTNIMA needs to know

- **No new settings.** `docker.env` stays exactly as it is.
- **No database migration.** The database structure is the same as in 1.0.2: nothing changes in the database when the new version starts.
- **How to update.**
  1. Make a backup first: the PostgreSQL database (`pg_dump`) and the `itpm_media` volume.
  2. Keep the 1.0.2 image (do not delete it) so that you can go back to it.
  3. Start the 1.1.0 image with the same `docker.env` and the same volumes.
- **Going back to 1.0.2** works on the same database, because 1.1.0 changes nothing in its structure. The only visible trace: the blocking reasons written with 1.1.0 show in 1.0.2 as ordinary comments starting with "[blocked]".
- **Configuration files.** Only the image tag changed in `docker-compose.yml` and `delivery/manifest.yml` (now `registry-mtnima.gov.mr/ddirepo/mchariee:1.1.0`). The French and Arabic comments of `manifest.yml`, which were garbled in the 1.0.2 copy, are readable again; no setting in it changed.
- **Browsers.** The file previews were checked in Edge and Chrome.

## 1.0.2

The version in use at MTNIMA when 1.1.0 was prepared.
