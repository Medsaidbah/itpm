"""Template mistakes that the other tests can't see, because the pages still work: a {# comment #} must open
and close on the same line. Over several lines, Django doesn't see a comment and prints it on the page as text
(use {% comment %} ... {% endcomment %} for longer notes)."""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from .base import TeamTestCase

TEMPLATES = Path(settings.BASE_DIR) / "templates"


class CommentTests(SimpleTestCase):
    def test_every_short_comment_closes_on_its_own_line(self):
        unclosed = []
        for template in sorted(TEMPLATES.rglob("*.html")) + sorted(TEMPLATES.rglob("*.txt")):
            for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), start=1):
                if line.count("{#") > line.count("#}"):
                    unclosed.append(f"{template.relative_to(TEMPLATES)}:{number}")
        self.assertEqual(unclosed, [], "Use {% comment %} ... {% endcomment %} for a comment over several lines")


class NoCommentOnThePageTests(TeamTestCase):
    def test_the_home_page_and_a_task_page_show_no_template_comment(self):
        self.login(self.omar)
        for url in ("/", self.task.get_absolute_url(), self.project.get_absolute_url(), "/projects/", "/dashboard/"):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), "{#")
