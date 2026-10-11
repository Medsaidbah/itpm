"""Profiles (Stage 17a): my profile (phone, picture, password), people's pages and pictures,
and who may see them: admins, the person, and the people they share a project with."""

import io
import os
import shutil
import tempfile

from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from PIL import Image

from accounts.models import User
from accounts.pictures import PICTURE_SIZE, make_profile_picture
from projects.models import Project, Task

from .base import TeamTestCase, days_from_today, picture_file


class MediaFolderTestCase(TeamTestCase):
    """Pictures are saved in a temporary folder, not in the real media folder."""

    def setUp(self):
        media_folder = tempfile.mkdtemp()
        settings_change = override_settings(MEDIA_ROOT=media_folder)
        settings_change.enable()
        self.addCleanup(settings_change.disable)
        self.addCleanup(shutil.rmtree, media_folder, ignore_errors=True)

    def give_picture(self, person):
        person.picture = make_profile_picture(picture_file())
        person.save()


class MyProfileTests(MediaFolderTestCase):
    def save_profile(self, **data):
        return self.client.post("/profile/", {"phone": "", **data}, follow=True)

    def test_the_page_shows_my_details(self):
        self.login(self.sara)
        response = self.client.get("/profile/")
        for text in ("Sara", "Ben", "sara@example.com", "Change my password"):
            self.assertContains(response, text)

    def test_my_phone_number(self):
        self.login(self.sara)
        self.assertContains(self.save_profile(phone="+222 22 12 34 56"), "Your profile is saved.")
        self.sara.refresh_from_db()
        self.assertEqual(self.sara.phone, "+222 22 12 34 56")

        response = self.save_profile(phone="call me")
        self.assertContains(response, "Enter a phone number with digits, spaces and + - ( ) only")
        self.sara.refresh_from_db()
        self.assertEqual(self.sara.phone, "+222 22 12 34 56")  # unchanged

    def test_names_email_and_role_are_changed_by_an_admin_only(self):
        self.login(self.sara)
        self.save_profile(first_name="Queen", email="queen@example.com", role="admin", phone="22 12 34 56")
        self.sara.refresh_from_db()
        self.assertEqual((self.sara.first_name, self.sara.email, self.sara.role, self.sara.phone),
                         ("Sara", "sara@example.com", "user", "22 12 34 56"))

    def test_a_picture_is_made_small_square_and_clean(self):
        self.login(self.sara)
        photo = picture_file("holiday-in-nouakchott.jpg", size=(1200, 800), image_format="JPEG", artist="Secret")
        self.save_profile(picture=photo)
        self.sara.refresh_from_db()
        self.assertNotIn("holiday", self.sara.picture.name)  # a random name, which tells nothing
        with Image.open(self.sara.picture.path) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (PICTURE_SIZE, PICTURE_SIZE)))
            self.assertEqual(dict(image.getexif()), {})  # the hidden details are gone

    def test_files_that_are_not_pictures_are_refused(self):
        self.login(self.sara)
        fake = SimpleUploadedFile("me.png", b"<script>alert('hi')</script>", content_type="image/png")
        response = self.save_profile(picture=fake)
        self.assertContains(response, "Upload a valid image.")
        self.sara.refresh_from_db()
        self.assertFalse(self.sara.picture)

    def test_pictures_that_are_too_big_are_refused(self):
        # A real 6 MB picture: random dots can't be made smaller by PNG
        noise = Image.frombytes("RGB", (1450, 1450), os.urandom(1450 * 1450 * 3))
        output = io.BytesIO()
        noise.save(output, "PNG")
        self.assertGreater(len(output.getvalue()), 5 * 1024 * 1024)
        self.login(self.sara)
        huge = SimpleUploadedFile("huge.png", output.getvalue(), content_type="image/png")
        self.assertContains(self.save_profile(picture=huge), "This picture is too big: pictures can be up to 5 MB.")
        self.sara.refresh_from_db()
        self.assertFalse(self.sara.picture)

    def test_replacing_or_removing_a_picture_deletes_the_old_file(self):
        self.login(self.sara)
        self.save_profile(picture=picture_file())
        self.sara.refresh_from_db()
        first = self.sara.picture.name
        storage = self.sara.picture.storage

        with self.captureOnCommitCallbacks(execute=True):  # files are deleted once the save is final
            self.save_profile(picture=picture_file("other.png"))
        self.assertFalse(storage.exists(first))
        self.sara.refresh_from_db()
        second = self.sara.picture.name
        self.assertTrue(storage.exists(second))

        with self.captureOnCommitCallbacks(execute=True):
            self.save_profile(remove_picture="on")
        self.sara.refresh_from_db()
        self.assertFalse(self.sara.picture)
        self.assertFalse(storage.exists(second))


# Checking a password is slow on purpose: these tests use a fast method instead (only here)
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PasswordTests(TeamTestCase):
    def test_changing_my_password(self):
        self.sara.set_password("old-Pass-123")
        self.sara.save()
        self.login(self.sara)
        response = self.client.post("/profile/password/", {
            "old_password": "wrong", "new_password1": "New-pass-4567", "new_password2": "New-pass-4567"})
        self.assertContains(response, "Your old password was entered incorrectly.")

        response = self.client.post("/profile/password/", {
            "old_password": "old-Pass-123", "new_password1": "New-pass-4567", "new_password2": "New-pass-4567"},
            follow=True)
        self.assertContains(response, "Your password is changed.")
        self.assertEqual(response.context["user"], self.sara)  # still logged in
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.check_password("New-pass-4567"))


class PersonPageTests(MediaFolderTestCase):
    def setUp(self):
        super().setUp()
        User.objects.filter(pk=self.omar.pk).update(phone="+222 36 00 11 22")
        # Omar also manages "Wi-Fi", with Karim. Sara isn't in that team.
        self.wifi = Project.objects.create(name="Wi-Fi", manager=self.omar)
        self.wifi.add_to_team(self.karim)
        Task.objects.create(project=self.wifi, title="Order access points", assignee=self.omar,
                            due_date=days_from_today(3))

    def test_who_sees_a_persons_page(self):
        for viewer, person, status in [
            (self.sara, self.omar, 200),  # teammates (Firewall)
            (self.amina, self.sara, 200),  # admins see everyone
            (self.karim, self.karim, 200),  # yourself
            (self.karim, self.sara, 404),  # no project in common: the page doesn't exist for him
            (self.sara, self.karim, 404),
        ]:
            with self.subTest(viewer=viewer.username, person=person.username):
                self.login(viewer)
                self.assertEqual(self.client.get(f"/people/{person.pk}/").status_code, status)

    def test_the_page_shows_details_and_only_shared_projects(self):
        self.login(self.sara)
        response = self.client.get(f"/people/{self.omar.pk}/")
        self.assertContains(response, "omar@example.com")
        self.assertContains(response, 'href="tel:+22236001122"')
        self.assertEqual(list(response.context["managed_projects"]), [self.project])  # not Wi-Fi
        self.assertNotContains(response, "Wi-Fi")
        self.assertNotContains(response, "Order access points")

        self.login(self.amina)  # an admin sees every project of the person
        response = self.client.get(f"/people/{self.omar.pk}/")
        self.assertEqual(set(response.context["managed_projects"]), {self.project, self.wifi})
        self.assertContains(response, "Order access points")

    def test_pictures_follow_the_same_rule(self):
        self.give_picture(self.sara)
        url = f"/people/{self.sara.pk}/picture/"
        self.login(self.omar)
        response = self.client.get(url)
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/jpeg"))
        b"".join(response.streaming_content)  # read it all, which closes the file
        self.login(self.karim)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.login(self.amina)
        self.assertEqual(self.client.get(f"/people/{self.omar.pk}/picture/").status_code, 404)  # no picture

    def test_pictures_are_not_reachable_through_the_files_address(self):
        self.give_picture(self.sara)
        self.login(self.sara)
        self.assertEqual(self.client.get(f"/media/{self.sara.picture.name}").status_code, 404)


class AvatarTests(MediaFolderTestCase):
    """{% avatar %} shows the picture to the people who may see the person, initials to others."""

    def render(self, viewer, template, person):
        request = RequestFactory().get("/")
        request.user = viewer
        return Template("{% load people %}" + template).render(Context({"request": request, "person": person}))

    def test_picture_or_initials(self):
        self.give_picture(self.sara)
        self.assertIn(f"/people/{self.sara.pk}/picture/?v=", self.render(self.omar, "{% avatar person %}", self.sara))
        not_allowed = self.render(self.karim, "{% avatar person %}", self.sara)
        self.assertNotIn("picture", not_allowed)
        self.assertIn(">SB<", not_allowed)  # Sara Ben's initials
        self.assertIn(">?<", self.render(self.omar, "{% avatar person %}", None))  # nobody: unassigned
        self.assertIn(">K<", self.render(AnonymousUser(), "{% avatar person %}", self.karim))  # no last name

    def test_names_link_to_the_page_only_when_allowed(self):
        self.assertEqual(self.render(self.omar, "{% person_link person %}", self.sara),
                         f'<a href="/people/{self.sara.pk}/">Sara Ben</a>')
        self.assertEqual(self.render(self.karim, "{% person_link person %}", self.sara), "Sara Ben")

    def test_pictures_appear_in_the_pages(self):
        self.give_picture(self.sara)
        picture = f"/people/{self.sara.pk}/picture/?v="
        self.login(self.sara)
        self.assertContains(self.client.get("/"), picture)  # the menu shows my own picture
        self.login(self.omar)  # Omar sees Sara's picture: in the team, on her task, on the board
        for page in (f"/projects/{self.project.pk}/", f"/tasks/{self.task.pk}/", f"/projects/{self.project.pk}/board/"):
            with self.subTest(page=page):
                self.assertContains(self.client.get(page), picture)
