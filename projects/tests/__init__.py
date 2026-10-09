"""Automated tests for the projects app.

A test is a small program that uses the app the way a person would (open a page,
send a form...) and checks the result. Each file covers one topic.

Run all the tests:          python manage.py test
Run one file:               python manage.py test projects.tests.test_permissions
Run one test:               python manage.py test projects.tests.test_permissions.TaskPermissionTests.test_assignee_can_change_status

Django creates an empty database just for the tests (test_itpm), runs them in it,
then deletes it. Your real data is never touched.
"""
