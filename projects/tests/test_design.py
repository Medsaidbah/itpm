"""The look of the site (Step 20): ministry bar, theme, login page, fonts and logo files."""

import re
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase

from accounts.models import User

from .base import TeamTestCase

STATIC = Path(settings.BASE_DIR) / "static"


class DesignTests(TeamTestCase):
    def test_pages_show_ministry_bar_and_theme(self):
        self.login(self.omar)
        html = self.client.get("/").content.decode()
        self.assertIn("css/theme.css", html)
        self.assertIn("gov-bar", html)
        self.assertIn("Directorate of Development and Interoperability", html)
        self.assertIn("img/logo.png", html)
        self.assertIn("img/favicon.png", html)
        self.assertIn("MTNMA", html)

    def test_ministry_names_are_translated(self):
        self.login(self.omar)
        for code, text in (("fr", "Direction du Développement et de l'Interopérabilité"),
                           ("ar", "مديرية التطوير والتشغيل البيني")):
            with self.subTest(code=code):
                User.objects.filter(pk=self.omar.pk).update(language=code)
                html = self.client.get("/").content.decode()
                self.assertIn(text, html)

    def test_theme_files_exist_and_fonts_resolve(self):
        css = (STATIC / "css" / "theme.css").read_text(encoding="utf-8")
        urls = re.findall(r"url\(['\"]?([^)'\"]+)['\"]?\)", css)
        self.assertTrue(urls)
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue((STATIC / "css" / url).resolve().exists())
        self.assertTrue((STATIC / "img" / "logo.png").exists())
        self.assertTrue((STATIC / "img" / "favicon.png").exists())


class LoginDesignTests(TestCase):
    def test_login_page_has_brand_panel_and_same_form(self):
        html = Client().get("/login/").content.decode()
        self.assertIn("auth-shell", html)
        self.assertIn("auth-brand", html)
        self.assertNotIn("gov-bar", html)
        for needle in ('id="id_username"', 'id="id_password"', 'name="next"', "csrfmiddlewaretoken"):
            self.assertIn(needle, html)
