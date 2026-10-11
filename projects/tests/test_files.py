"""Files added to tasks: uploading, downloading (only for the project's team), size limit, deleting."""

import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from projects.forms import MAX_UPLOAD_MB
from projects.models import Attachment

from .base import TeamTestCase


class FileTests(TeamTestCase):
    def setUp(self):
        # Save the test files in a temporary folder, not in the real media folder
        self.media_folder = tempfile.mkdtemp()
        settings_change = override_settings(MEDIA_ROOT=self.media_folder)
        settings_change.enable()
        self.addCleanup(settings_change.disable)
        self.addCleanup(shutil.rmtree, self.media_folder, ignore_errors=True)

    def upload(self, name="quote.pdf", content=b"%PDF-1.4 test file"):
        return self.client.post(f"/tasks/{self.task.pk}/files/add/",
                                {"file": SimpleUploadedFile(name, content)})

    def test_upload_and_download(self):
        self.login(self.sara)  # everyone in the project's team can add files to its tasks
        response = self.upload()
        self.assertRedirects(response, f"/tasks/{self.task.pk}/#files", fetch_redirect_response=False)
        attachment = Attachment.objects.get(task=self.task)
        self.assertEqual((attachment.filename, attachment.uploaded_by), ("quote.pdf", self.sara))

        response = self.client.get(attachment.file.url)
        self.assertEqual(response.status_code, 200)
        # "attachment": the browser saves the file instead of opening it inside the app
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="quote.pdf"')
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test file")
        response.close()  # close the file (Windows can't delete open files)

    def test_downloads_need_a_login_and_the_team(self):
        self.login(self.sara)
        self.upload()
        url = Attachment.objects.get(task=self.task).file.url
        self.client.logout()
        self.assertRedirects(self.client.get(url), f"/login/?next={url}")
        self.login(self.karim)  # not in the project's team: the file doesn't exist for him
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(f"/tasks/{self.task.pk}/files/add/").status_code, 404)

    def test_only_files_of_tasks_can_be_downloaded(self):
        self.login(self.sara)
        self.assertEqual(self.client.get("/media/../config/settings.py").status_code, 404)
        self.assertEqual(self.client.get("/media/attachments/not-a-file.pdf").status_code, 404)

    def test_files_that_are_too_big_are_refused(self):
        self.login(self.sara)
        too_big = b"0" * (MAX_UPLOAD_MB * 1024 * 1024 + 1)
        response = self.upload("huge.iso", too_big)
        self.assertFalse(Attachment.objects.exists())
        response = self.client.get(f"/tasks/{self.task.pk}/")
        self.assertContains(response, f"This file is too big: files can be up to {MAX_UPLOAD_MB} MB.")

    def test_deleting_removes_the_file_from_the_disk(self):
        self.login(self.sara)
        self.upload()
        attachment = Attachment.objects.get(task=self.task)
        storage, name = attachment.file.storage, attachment.file.name
        self.assertTrue(storage.exists(name))

        self.project.add_to_team(self.karim)
        self.login(self.karim)  # in the team, but not the person who added it
        self.assertEqual(self.client.post(f"/files/{attachment.pk}/delete/").status_code, 403)

        self.login(self.sara)
        # The file is deleted from the disk once the database change is final ("on commit")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/files/{attachment.pk}/delete/")
        self.assertFalse(Attachment.objects.exists())
        self.assertFalse(storage.exists(name))
