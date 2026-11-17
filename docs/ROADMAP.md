# Mchariee roadmap

Approved on 2026-11-17. It answers the director's 14 suggestions for the versions after 1.0.2.
Update the "Status" columns as work lands; change the plan only with the product owner's approval.

## Why the releases are split this way

The live app (1.0.2) runs at MTNIMA with real data that nobody here has a copy of. MTNIMA receives each new
Docker image and runs it on its own database, and the migrations run automatically at start. So:

- Release 1.1 contains only improvements that need **no database change**.
- Each later release brings **one** database change at a time.
- Every migration is additive: new fields are optional or have a default, existing rows keep working
  unchanged, and no migration deletes or rewrites existing data.
- The test `projects/tests/test_migrations.py` fails when a model changes without its migration.
- Before a release with a migration, rehearse it outside the project folder: a database at the previous
  schema (from `master`), filled with data, then migrated with the new branch.

## Release 1.1: no database change (branch `v1.1`)

One feature per commit, each with its tests, the full test suite passing, and the en/fr/ar translations.

| # | Feature | Idea | Status |
|---|---|---|---|
| 1 | Clickable web addresses in comments and descriptions | 4 | Done (513c22a) |
| 2 | Team pictures in the project list | 10 | Done (9e69d6c) |
| 3 | Delay detection: expected vs actual progress | 7 | To do |
| 4 | Highlight late (red) and behind-schedule (amber) projects | 8 | To do |
| 5 | Project count cards on the home page | 1 | To do |
| 6 | Preview attachments (PDF and images) | 3 | To do |
| 7 | Contribution summary on people's pages | 11 | To do |
| 8 | A reason is required when a task is blocked | 5 (part) | To do |

## Later releases: one database change each

| Release | Feature | Idea |
|---|---|---|
| 1.2 | Read-only executive role: sees every project, indicator, team, activity, delay and blocker; edits nothing | 14 |
| 1.3 | Project type (internal / external / not specified), external partner, budget and cost so far | 12 |
| 1.4 | Task weights (default 1, so today's progress figures don't change) | 6 |
| 1.5 | Archive instead of delete for projects and tasks; block records (reason, start, expected end) | 5 |
| 1.6 | Manager approval before a task is done, as an option per project, off by default | 9 |
| 1.7 | Task contributors besides the responsible person | 2 |
| 1.8+ | Requests / ticketing, once its requirements are settled (who submits, who sorts, how it becomes work) | 13 |

## Decisions

1. **Indicators (item 5):** the project count cards go on the home page (Planned, In progress, On hold,
   Completed, Late, and "Blocked" = an open project with at least one blocked task). The Dashboard stays,
   and the home page links to it. Each card links to the filtered project list and respects the visibility
   rules: people only count the projects they can see.
2. **Delay margin (item 3):** a project is "behind schedule" when its progress is more than **10 points**
   below the expected progress. The 10 is one named constant in the code. Expected progress is the share of
   tasks due by today; when the tasks have no due dates, it is the share of time elapsed between the
   project's start and end dates. Projects without dates show no delay.
3. **Who sees contribution figures (item 7):** admins see everyone; a project's managers see the members of
   their own projects; everyone else sees only their own figures. The future read-only role (1.2) sees
   everyone.
4. **Blocking reason (item 8):** included in 1.1, as its last item. The reason is saved as a comment on the
   task. It must be tested both on the board (dragging a card to "Blocked") and on the task form and
   status buttons.

Also agreed: for the attachment preview (item 6), the results in the browsers MTNIMA uses are listed
before the feature is committed.
