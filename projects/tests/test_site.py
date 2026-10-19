"""The whole site: no files from other websites, friendly error pages, safety headers."""

import re

from django.template.loader import render_to_string

from .base import TeamTestCase


class SiteTests(TeamTestCase):
    def test_pages_load_nothing_from_other_websites(self):
        # Everything (styles, scripts, fonts) comes from the app itself, in static/vendor/.
        # The app then works without depending on another website, and nobody else can change it.
        self.login(self.omar)
        pages = ["/", "/projects/", f"/projects/{self.project.pk}/", f"/projects/{self.project.pk}/board/",
                 f"/projects/{self.project.pk}/timeline/", "/dashboard/", "/activity/", "/my-tasks/"]
        for page in pages:
            with self.subTest(page=page):
                html = self.client.get(page).content.decode()
                outside = re.findall(r'<(?:script|link)[^>]+(?:src|href)="(https?://[^"]+)"', html)
                self.assertEqual(outside, [])
                self.assertIn("/static/vendor/tabler/tabler.min.css", html)

    def test_friendly_page_for_unknown_addresses(self):
        self.login(self.sara)
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "This page doesn't exist", status_code=404)

    def test_error_page_needs_nothing_else(self):
        # The 500 page is shown when something is broken, so it must work on its own
        html = render_to_string("500.html")
        self.assertIn("Something went wrong on our side", html)
        self.assertNotIn("{%", html)

    def test_safety_headers(self):
        self.login(self.sara)
        response = self.client.get("/")
        self.assertEqual(response["X-Frame-Options"], "DENY")  # no showing our pages inside another site
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")


class HealthCheckTests(TeamTestCase):
    """/healthz/ is for Docker and monitoring: public, shows nothing, tells if the database answers."""

    def test_answers_ok_without_login(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_shows_no_data(self):
        self.assertNotIn(self.sara.username.encode(), self.client.get("/healthz/").content)

    def test_says_503_when_the_database_fails(self):
        from unittest import mock

        with mock.patch("config.health.connection.cursor", side_effect=Exception("down")):
            with self.assertLogs("config.health", level="ERROR"):
                response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 503)

    def test_is_not_sent_to_https(self):
        # inside the container the check uses plain http, even when the site itself forces https
        with self.settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^healthz/$"]):
            self.assertEqual(self.client.get("/healthz/").status_code, 200)
            self.assertEqual(self.client.get("/login/").status_code, 301)
