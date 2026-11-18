"""Task files shown inside the browser (release 1.1): PDFs in the browser's viewer, pictures as thumbnails.
Only PDF, PNG, JPEG, GIF and WebP files, recognised from their content; everything else is only downloaded,
exactly as before. The rules are written at the top of projects/previews.py."""

import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from projects.models import Attachment
from projects.previews import preview_type

from .base import TeamTestCase, picture_file

PDF_BYTES = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
HTML_BYTES = b"<!doctype html><html><body><script>alert(document.cookie)</script></body></html>"
SVG_BYTES = (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
             b"<script>alert(document.cookie)</script></svg>")
STRICT_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


def picture_bytes(image_format):
    return picture_file(image_format=image_format).read()


class PreviewTypeTests(SimpleTestCase):
    """The type is read from the first bytes of the file."""

    def test_the_five_kinds_of_file(self):
        self.assertEqual(preview_type(PDF_BYTES[:12]), "application/pdf")
        self.assertEqual(preview_type(picture_bytes("PNG")[:12]), "image/png")
        self.assertEqual(preview_type(picture_bytes("JPEG")[:12]), "image/jpeg")
        self.assertEqual(preview_type(picture_bytes("GIF")[:12]), "image/gif")
        self.assertEqual(preview_type(picture_bytes("WEBP")[:12]), "image/webp")
        self.assertEqual(preview_type(b"GIF87a" + b"\0" * 6), "image/gif")  # the older kind of GIF

    def test_everything_else(self):
        for head in (HTML_BYTES, SVG_BYTES, b"", b"hello", b"PK\x03\x04 a Word file", b"RIFF\0\0\0\0WAVEfmt ",
                     b" %PDF-1.4", b"\x89PNX\r\n\x1a\n"):
            with self.subTest(head=head[:12]):
                self.assertIsNone(preview_type(head[:12]))


class PreviewTestCase(TeamTestCase):
    def setUp(self):
        # Save the test files in a temporary folder, not in the real media folder
        self.media_folder = tempfile.mkdtemp()
        settings_change = override_settings(MEDIA_ROOT=self.media_folder)
        settings_change.enable()
        self.addCleanup(settings_change.disable)
        self.addCleanup(shutil.rmtree, self.media_folder, ignore_errors=True)

    def attach(self, name, content):
        return Attachment.objects.create(task=self.task, uploaded_by=self.sara, file=SimpleUploadedFile(name, content))

    def preview_url(self, attachment):
        return f"/files/{attachment.pk}/preview/"

    def get(self, url):
        """The response and its whole content. Reading a file response to the end makes the test client close it
        (Windows can't delete open files). Don't call response.close() again: it would close the database
        connection of the test."""
        response = self.client.get(url)
        content = b"".join(response.streaming_content) if response.streaming else response.content
        return response, content


class PreviewTests(PreviewTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.sara)

    def test_each_allowed_kind_is_shown_in_the_browser(self):
        files = [
            ("quote.pdf", PDF_BYTES, "application/pdf"),
            ("photo.png", picture_bytes("PNG"), "image/png"),
            ("photo.jpg", picture_bytes("JPEG"), "image/jpeg"),
            ("photo.gif", picture_bytes("GIF"), "image/gif"),
            ("photo.webp", picture_bytes("WEBP"), "image/webp"),
        ]
        for name, content, content_type in files:
            with self.subTest(name=name):
                response, body = self.get(self.preview_url(self.attach(name, content)))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], content_type)
                self.assertEqual(response["Content-Disposition"], f'inline; filename="{name}"')
                self.assertEqual(body, content)

    def test_the_strict_headers(self):
        for name, content in (("photo.png", picture_bytes("PNG")), ("quote.pdf", PDF_BYTES)):
            with self.subTest(name=name):
                response, _ = self.get(self.preview_url(self.attach(name, content)))
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response["Content-Security-Policy"], STRICT_CSP)
                self.assertEqual(response["Cross-Origin-Resource-Policy"], "same-origin")
                self.assertEqual(response["Cache-Control"], "private")
                self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_the_type_comes_from_the_content_not_the_name(self):
        response, body = self.get(self.preview_url(self.attach("notes.txt", PDF_BYTES)))
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(response["Content-Disposition"], 'inline; filename="notes.txt"')

        png = picture_bytes("PNG")
        response, body = self.get(self.preview_url(self.attach("scan.pdf", png)))
        self.assertEqual((response.status_code, response["Content-Type"], body), (200, "image/png", png))

    def test_a_web_page_named_like_a_picture_is_not_shown(self):
        fake = self.attach("cat.png", HTML_BYTES)
        response, body = self.get(self.preview_url(fake))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"alert(document.cookie)", body)  # nothing of the file

    def test_a_picture_with_a_web_page_hidden_inside_is_still_only_a_picture(self):
        tricky = self.attach("trick.png", b"\x89PNG\r\n\x1a\n" + HTML_BYTES)
        response, _ = self.get(self.preview_url(tricky))
        # The browser must treat it as a (broken) picture, never as a page: exact type, no guessing, sandboxed
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Security-Policy"], STRICT_CSP)

    def test_svg_and_html_are_only_downloaded(self):
        for name, content, download_type in (("logo.svg", SVG_BYTES, "image/svg+xml"),
                                             ("page.html", HTML_BYTES, "text/html")):
            with self.subTest(name=name):
                attachment = self.attach(name, content)
                response, body = self.get(self.preview_url(attachment))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(b"alert(document.cookie)", body)  # nothing of the file
                # The download is exactly as before: saved by the browser, never opened in the app
                response, body = self.get(attachment.file.url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Disposition"], f'attachment; filename="{name}"')
                self.assertEqual(response["Content-Type"], download_type)
                self.assertEqual(body, content)

    def test_other_files_are_only_downloaded(self):
        for name, content in (("readme.txt", b"hello"), ("empty.png", b""), ("report.docx", b"PK\x03\x04 Word")):
            with self.subTest(name=name):
                response, _ = self.get(self.preview_url(self.attach(name, content)))
                self.assertEqual(response.status_code, 404)

    def test_the_download_of_a_pdf_or_a_picture_is_unchanged(self):
        for name, content, content_type in (("quote.pdf", PDF_BYTES, "application/pdf"),
                                            ("photo.png", picture_bytes("PNG"), "image/png")):
            with self.subTest(name=name):
                response, body = self.get(self.attach(name, content).file.url)
                self.assertEqual(response["Content-Disposition"], f'attachment; filename="{name}"')
                self.assertEqual(response["Content-Type"], content_type)
                self.assertNotIn("Content-Security-Policy", response)  # the download got no new header
                self.assertEqual(body, content)

    def test_a_file_missing_from_the_disk(self):
        attachment = self.attach("photo.png", picture_bytes("PNG"))
        attachment.file.storage.delete(attachment.file.name)
        response, _ = self.get(self.preview_url(attachment))
        self.assertEqual(response.status_code, 404)
        page = self.client.get(self.task.get_absolute_url())
        self.assertContains(page, "File missing")
        self.assertNotContains(page, self.preview_url(attachment))

    def test_a_file_that_does_not_exist(self):
        response, _ = self.get("/files/999999/preview/")
        self.assertEqual(response.status_code, 404)


class PreviewPermissionTests(PreviewTestCase):
    def setUp(self):
        super().setUp()
        self.picture = self.attach("photo.png", picture_bytes("PNG"))
        self.pdf = self.attach("quote.pdf", PDF_BYTES)

    def test_the_team_and_the_admins_get_the_previews(self):
        for person in (self.sara, self.omar, self.amina):
            for attachment in (self.picture, self.pdf):
                with self.subTest(person=person.username, file=attachment.filename):
                    self.login(person)
                    response, _ = self.get(self.preview_url(attachment))
                    self.assertEqual(response.status_code, 200)

    def test_someone_outside_the_project_gets_nothing(self):
        self.login(self.karim)
        for attachment in (self.picture, self.pdf):
            with self.subTest(file=attachment.filename):
                response, body = self.get(self.preview_url(attachment))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(b"%PDF", body)
                self.assertNotIn(b"PNG", body)

    def test_a_visitor_who_is_not_logged_in_gets_nothing(self):
        for attachment in (self.picture, self.pdf):
            with self.subTest(file=attachment.filename):
                url = self.preview_url(attachment)
                response, body = self.get(url)
                self.assertRedirects(response, f"/login/?next={url}", fetch_redirect_response=False)
                self.assertNotIn(b"%PDF", body)
                self.assertNotIn(b"PNG", body)


class TaskPageTests(PreviewTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.sara)
        self.picture = self.attach("photo.png", picture_bytes("PNG"))
        self.pdf = self.attach("quote.pdf", PDF_BYTES)
        self.fake = self.attach("cat.png", HTML_BYTES)
        self.svg = self.attach("logo.svg", SVG_BYTES)

    def page(self):
        response = self.client.get(self.task.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        return response

    def test_a_picture_shows_as_a_thumbnail_that_opens_in_a_new_tab(self):
        url = self.preview_url(self.picture)
        self.assertContains(self.page(), f"""
            <a href="{url}" target="_blank" rel="noopener" class="flex-shrink-0" title="Open the picture in a new tab">
              <img src="{url}" alt="photo.png" class="file-thumb" loading="lazy">
            </a>""", html=True)

    def test_a_pdf_gets_a_preview_button_that_opens_in_a_new_tab(self):
        url = self.preview_url(self.pdf)
        self.assertContains(self.page(), f"""
            <a href="{url}" target="_blank" rel="noopener" class="btn btn-sm" title="Open the PDF in a new tab">
              <i class="ti ti-eye me-1"></i>Preview
            </a>""", html=True)

    def test_files_that_cannot_be_shown_get_neither(self):
        page = self.page()
        for attachment in (self.fake, self.svg):
            with self.subTest(file=attachment.filename):
                self.assertNotContains(page, self.preview_url(attachment))

    def test_every_name_still_downloads_the_file(self):
        page = self.page()
        for attachment in (self.picture, self.pdf, self.fake, self.svg):
            with self.subTest(file=attachment.filename):
                self.assertContains(page, f'<a href="{attachment.file.url}" class="d-block text-truncate"')

    def test_in_arabic(self):
        self.sara.language = "ar"
        self.sara.save()
        page = self.page()
        self.assertContains(page, 'dir="rtl"')
        self.assertContains(page, '<i class="ti ti-eye me-1"></i>معاينة')
        self.assertContains(page, 'title="فتح الصورة في علامة تبويب جديدة"')
        self.assertContains(page, 'title="فتح ملف PDF في علامة تبويب جديدة"')

    def test_in_french(self):
        self.sara.language = "fr"
        self.sara.save()
        page = self.page()
        self.assertContains(page, '<i class="ti ti-eye me-1"></i>Aperçu')
        self.assertContains(page, "title=\"Ouvrir l'image dans un nouvel onglet\"")
