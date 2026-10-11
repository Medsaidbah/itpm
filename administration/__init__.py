"""The Administration section (Stage 17b): pages for the app's admins only.

- Users: search, create, edit, set a new password, deactivate or reactivate.
  People are never deleted here, so their history, tasks and comments stay.
- Each person's projects: add them to a project's team or take them out of it.
- Every project with its manager and team, and every task with filters.
- Logins locked after too many wrong passwords: see them and unlock them.

Every page checks that the person is an admin (see admin_required in views.py):
hiding the menu item from the others is not what protects these pages.

This folder has no database tables (no models.py), so it doesn't need a line in
INSTALLED_APPS: config/urls.py sends the /administration/ addresses here.
"""
