# Mchariee – changelog

## 1.4.0 (2026-11-24)

### What changes for the people who use Mchariee

- **Task weights.** A task can weigh 1 (small), 2, 3 or 5 (very large). The project's managers and admins set it
  in the task form; everyone who sees the task sees it, also in the task export. A project's progress and the
  progress expected by today now take the weights into account, so a large task counts more than a small one.

### What MTNIMA needs to know

- **No new settings.** `docker.env` stays exactly as it is.
- **Database migrations, by the version you run now.** They apply by themselves when the new version starts, in
  this order. Nothing is deleted, renamed or rewritten.
  - **From 1.3.0:** only `projects/0011`. It adds two columns, the weight of the tasks and of their history, with
    the default 1 for every existing task.
  - **From 1.2.0:** also `projects/0010` first (three new columns on the projects and their history: type,
    partner, budget).
  - **From 1.1.0:** also `accounts/0008` first (it only adds the "Executive" role to the list of roles: no SQL,
    no data changed).
- **Progress stays the same.** Every existing task gets the weight 1, and with every weight at 1 every progress and
  expected-progress figure is exactly what it was before. Figures only change when someone gives a task another
  weight.
- **How to update.**
  1. Make a backup first: the PostgreSQL database (`pg_dump`) and the `itpm_media` volume.
  2. Keep the previous image (do not delete it) so that you can go back to it.
  3. Start the 1.4.0 image with the same `docker.env` and the same volumes.
- **Going back one version works on the same database.** 1.3.0 keeps working after `projects/0011`: it ignores the
  new column, and the database gives the weight 1 to the tasks 1.3.0 creates. Weights entered meanwhile stay in the
  database and come back with 1.4.0.
- **Configuration files.** Only the image tag changed in `docker-compose.yml` and `delivery/manifest.yml` (now
  `registry-mtnima.gov.mr/ddirepo/mchariee:1.4.0`).

## 1.3.0 (2026-11-24)

### What changes for the people who use Mchariee

- **Project type and partner.** A project can be marked Internal or External (existing projects are "Not specified"),
  and an external project can name its partner. Everyone who sees the project sees them.
- **Budget** (in MRU). The project's manager and admins enter it; co-managers, executives and admins see it too.
  Ordinary team members never see it.
- **For admins and executives:** a budget column in the project list and its export, and the total budget of the
  active projects on the Dashboard, with how many of them have a budget. The project export also gets a "Type"
  column for everyone.

### What MTNIMA needs to know

- **No new settings.** `docker.env` stays exactly as it is.
- **Database migrations.** They apply by themselves when the new version starts.
  - **Updating from 1.2.0:** one migration, `projects/0010`. It adds three columns to the projects and to their
    history (type, partner, budget). Nothing is deleted, renamed or rewritten; existing projects get the type
    "Not specified", no partner and no budget.
  - **Updating from 1.1.0:** two migrations, `accounts/0008` first (it only adds the "Executive" role to the list
    of roles: no SQL, no data changed), then `projects/0010` as above.
- **How to update.**
  1. Make a backup first: the PostgreSQL database (`pg_dump`) and the `itpm_media` volume.
  2. Keep the previous image (do not delete it) so that you can go back to it.
  3. Start the 1.3.0 image with the same `docker.env` and the same volumes.
- **Going back one version works on the same database.**
  - **To 1.2.0:** it keeps working after `projects/0010`. It ignores the three new columns, and the database fills
    in their defaults when 1.2.0 creates or saves a project. The types, partners and budgets entered meanwhile stay
    in the database (1.2.0 doesn't show them) and come back with 1.3.0.
  - **To 1.1.0:** first give every executive another role (Administration > Users): 1.1.0 doesn't know the
    "Executive" role, so an executive would see nothing there.
- **Configuration files.** Only the image tag changed in `docker-compose.yml` and `delivery/manifest.yml` (now
  `registry-mtnima.gov.mr/ddirepo/mchariee:1.3.0`).

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
