"""Web addresses in comments and descriptions are clickable (release 1.1).
What people typed stays plain text: only http://, https://, www. and email addresses become links."""

from django.template import Context, Template
from django.test import SimpleTestCase

from projects.models import Comment
from projects.templatetags.text import LINK_TEXT_LIMIT, linkify

from .base import TeamTestCase

WEB_LINK = '<a dir="ltr" target="_blank" rel="noopener noreferrer" href="{url}">{text}</a>'


def web_link(url, text=None):
    return WEB_LINK.format(url=url, text=text or url)


class LinkifyTests(SimpleTestCase):
    def test_a_web_address_becomes_a_link_that_opens_in_a_new_tab(self):
        self.assertEqual(
            linkify("See https://example.org/guide for details"),
            f"See {web_link('https://example.org/guide')} for details",
        )

    def test_plain_http_and_www_addresses(self):
        self.assertEqual(linkify("http://intranet.mr/a"), web_link("http://intranet.mr/a"))
        self.assertEqual(linkify("www.example.org"), web_link("http://www.example.org", "www.example.org"))

    def test_the_punctuation_after_an_address_is_not_part_of_it(self):
        self.assertEqual(linkify("Done: https://example.org."), f"Done: {web_link('https://example.org')}.")
        self.assertEqual(linkify("(https://example.org)"), f"({web_link('https://example.org')})")

    def test_the_arabic_comma_semicolon_and_question_mark_end_the_address(self):
        for mark in "،؛؟":
            with self.subTest(mark=mark):
                self.assertEqual(
                    linkify(f"انظر https://example.org{mark} ثم"),
                    f"انظر {web_link('https://example.org')}{mark} ثم",
                )

    def test_an_email_address_opens_the_mail_program_in_the_same_tab(self):
        self.assertEqual(
            linkify("Write to it@example.org"),
            'Write to <a dir="ltr" href="mailto:it@example.org">it@example.org</a>',
        )

    def test_html_people_type_stays_text(self):
        result = linkify('<script>alert(1)</script> <a href="https://evil.example">x</a>')
        self.assertNotIn("<script>", result)
        self.assertNotIn('<a href="https://evil.example">', result)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", result)

    def test_dangerous_kinds_of_address_are_not_links(self):
        for text in ["javascript:alert(1)", "data:text/html,<b>x</b>", "vbscript:msgbox(1)", "file:///C:/secret.txt"]:
            with self.subTest(text=text):
                self.assertNotIn("<a", linkify(text))

    def test_quotes_in_an_address_cannot_add_attributes(self):
        result = linkify('https://example.org/"onmouseover="alert(1)')
        self.assertNotIn('"onmouseover="', result)
        self.assertNotIn(" onmouseover=", result)

    def test_a_long_address_is_shortened_on_the_page_but_the_link_is_whole(self):
        url = "https://example.org/" + "a" * 100
        result = linkify(url)
        self.assertIn(f'href="{url}"', result)
        shown = result.split(">", 1)[1].rsplit("</a>", 1)[0]
        self.assertEqual(len(shown), LINK_TEXT_LIMIT)
        self.assertTrue(shown.endswith("…"))

    def test_text_without_addresses_is_unchanged_apart_from_escaping(self):
        self.assertEqual(linkify("Call Omar & Sara tomorrow"), "Call Omar &amp; Sara tomorrow")

    def test_line_breaks_are_still_kept_after_the_links(self):
        template = Template("{% load text %}{{ body|linkify|linebreaksbr }}")
        result = template.render(Context({"body": "First line\nhttps://example.org\n<b>bold?</b>"}))
        self.assertEqual(
            result, f"First line<br>{web_link('https://example.org')}<br>&lt;b&gt;bold?&lt;/b&gt;"
        )


class LinksInPagesTests(TeamTestCase):
    def setUp(self):
        self.login(self.sara)

    def test_a_comment(self):
        Comment.objects.create(task=self.task, author=self.omar, body="Manual: https://example.org/manual")
        response = self.client.get(self.task.get_absolute_url())
        self.assertContains(response, web_link("https://example.org/manual"), html=True)

    def test_a_comment_with_html_stays_text(self):
        Comment.objects.create(task=self.task, author=self.omar, body="<b>https://example.org</b>")
        response = self.client.get(self.task.get_absolute_url())
        self.assertNotContains(response, "<b>https://")
        self.assertContains(response, "&lt;b&gt;")

    def test_the_task_description(self):
        self.task.description = "Steps:\nhttps://example.org/steps"
        self.task.save()
        response = self.client.get(self.task.get_absolute_url())
        self.assertContains(response, web_link("https://example.org/steps"), html=True)

    def test_the_project_description_on_its_page_and_on_its_report(self):
        self.project.description = "Specs on www.example.org"
        self.project.save()
        link = web_link("http://www.example.org", "www.example.org")
        self.assertContains(self.client.get(self.project.get_absolute_url()), link, html=True)
        self.assertContains(self.client.get(f"/projects/{self.project.pk}/report/"), link, html=True)

    def test_an_arabic_page(self):
        self.sara.language = "ar"
        self.sara.save()
        Comment.objects.create(task=self.task, author=self.omar, body="الدليل هنا: https://example.org/دليل، شكرا")
        response = self.client.get(self.task.get_absolute_url())
        self.assertContains(response, 'dir="rtl"')
        # The address keeps its Arabic part, loses the Arabic comma, and reads left to right
        self.assertContains(response, 'dir="ltr" target="_blank" rel="noopener noreferrer"')
        self.assertContains(response, 'href="https://example.org/%D8%AF%D9%84%D9%8A%D9%84"')
        self.assertContains(response, "https://example.org/دليل</a>، شكرا")
