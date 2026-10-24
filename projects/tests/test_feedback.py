"""Step 25: everyone can rate the app and send a remark; only admins read them."""

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from accounts.models import Feedback, User

from .base import TeamTestCase
from .test_languages import choose


class FeedbackTestCase(TeamTestCase):
    def setUp(self):
        cache.clear()
        User.objects.update(language="en")

    def send(self, who=None, **data):
        self.login(who or self.sara)
        return self.client.post(reverse("feedback"), {"rating": "4", "kind": "idea", "message": "More colors", **data})


class SendingTests(FeedbackTestCase):
    def test_the_form_opens_for_everybody_logged_in(self):
        for person in (self.amina, self.omar, self.sara, self.karim):
            self.login(person)
            self.assertContains(self.client.get(reverse("feedback")), "Your rating")

    def test_the_menu_links_to_it_with_the_current_page(self):
        self.login(self.sara)
        response = self.client.get("/my-tasks/")
        self.assertContains(response, f"{reverse('feedback')}?from=/my-tasks/")

    def test_a_remark_is_saved_with_the_author(self):
        response = self.send(page="/my-tasks/")
        self.assertRedirects(response, "/my-tasks/")
        note = Feedback.objects.get()
        self.assertEqual(
            (note.author, note.rating, note.kind, note.message, note.page),
            (self.sara, 4, "idea", "More colors", "/my-tasks/"),
        )

    def test_without_a_name_nobody_is_saved(self):
        self.send(anonymous="on")
        self.assertIsNone(Feedback.objects.get().author)

    def test_a_thank_you_shows_after_sending(self):
        self.login(self.sara)
        response = self.client.post(reverse("feedback"), {"rating": "5", "kind": "praise"}, follow=True)
        self.assertContains(response, "Thank you!")

    def test_the_rating_is_needed_and_between_1_and_5(self):
        for bad in ("", "0", "6", "abc"):
            with self.subTest(rating=bad):
                response = self.send(rating=bad)
                self.assertEqual(response.status_code, 200)
        self.assertFalse(Feedback.objects.exists())

    def test_the_message_is_optional(self):
        self.send(message="")
        self.assertEqual(Feedback.objects.get().message, "")

    def test_a_very_long_message_is_refused(self):
        self.send(message="x" * (Feedback.MAX_MESSAGE + 1))
        self.assertFalse(Feedback.objects.exists())

    def test_an_unknown_kind_is_refused(self):
        self.send(kind="spam")
        self.assertFalse(Feedback.objects.exists())

    def test_only_addresses_of_this_site_are_kept(self):
        for outside in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
            with self.subTest(page=outside):
                response = self.send(page=outside)
                self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(set(Feedback.objects.values_list("page", flat=True)), {""})

    def test_the_from_link_ignores_other_sites(self):
        self.login(self.sara)
        response = self.client.get(reverse("feedback"), {"from": "https://evil.example/"})
        self.assertEqual(response.context["form"].initial["page"], "")
        response = self.client.get(reverse("feedback"), {"from": "/projects/"})
        self.assertEqual(response.context["form"].initial["page"], "/projects/")

    def test_five_an_hour_at_most(self):
        for _ in range(5):
            self.send()
        response = self.send()
        self.assertContains(response, "several remarks")
        self.assertEqual(Feedback.objects.count(), 5)
        self.send(self.omar)  # somebody else is not limited
        self.assertEqual(Feedback.objects.count(), 6)

    def test_someone_not_logged_in_goes_to_the_login_page(self):
        self.client.logout()
        response = self.client.post(reverse("feedback"), {"rating": "5"})
        self.assertRedirects(response, "/login/?next=/feedback/", fetch_redirect_response=False)
        self.assertFalse(Feedback.objects.exists())

    def test_the_form_is_translated(self):
        for language, text in (("fr", "Votre note"), ("ar", "تقييمك")):
            choose(self.sara, language)
            self.login(self.sara)
            self.assertContains(self.client.get(reverse("feedback")), text)

    def test_deleting_the_account_keeps_the_remark_without_a_name(self):
        self.send()
        self.sara.delete()
        self.assertIsNone(Feedback.objects.get().author)


class AdminPageTests(FeedbackTestCase):
    def setUp(self):
        super().setUp()
        self.good = Feedback.objects.create(author=self.sara, rating=5, kind="praise", message="Great tool")
        self.bad = Feedback.objects.create(author=None, rating=2, kind="problem", message="Board is slow")

    def page(self, **query):
        self.login(self.amina)
        return self.client.get(reverse("admin_feedback"), query)

    def test_only_admins_open_it(self):
        for person in (self.omar, self.sara, self.karim):
            self.login(person)
            self.assertEqual(self.client.get(reverse("admin_feedback")).status_code, 403)
            self.assertEqual(self.client.post(reverse("admin_feedback_review", args=[self.good.pk])).status_code, 403)
        self.good.refresh_from_db()
        self.assertIsNone(self.good.reviewed_at)

    def test_it_shows_the_remarks_the_names_and_anonymous(self):
        response = self.page()
        self.assertContains(response, "Great tool")
        self.assertContains(response, "Board is slow")
        self.assertContains(response, "Sara Ben")
        self.assertContains(response, "Anonymous")

    def test_the_average_and_the_count(self):
        response = self.page()
        self.assertEqual(response.context["total"], 2)
        self.assertEqual(response.context["average"], 3.5)
        self.assertContains(response, "3.5 / 5")

    def test_the_distribution(self):
        counts = {row["stars"]: row["count"] for row in self.page().context["distribution"]}
        self.assertEqual(counts, {5: 1, 4: 0, 3: 0, 2: 1, 1: 0})

    def test_filters(self):
        self.assertEqual([n.pk for n in self.page(rating="2").context["page"]], [self.bad.pk])
        self.assertEqual([n.pk for n in self.page(kind="praise").context["page"]], [self.good.pk])
        self.good.reviewed_at = timezone.now()
        self.good.save()
        self.assertEqual([n.pk for n in self.page(state="new").context["page"]], [self.bad.pk])
        self.assertEqual([n.pk for n in self.page(state="reviewed").context["page"]], [self.good.pk])

    def test_an_unknown_filter_is_ignored(self):
        self.assertEqual(len(self.page(rating="9", kind="x", state="y").context["page"]), 2)

    def test_marking_as_read_and_back(self):
        self.login(self.amina)
        self.client.post(reverse("admin_feedback_review", args=[self.good.pk]))
        self.good.refresh_from_db()
        self.assertEqual(self.good.reviewed_by, self.amina)
        self.assertIsNotNone(self.good.reviewed_at)
        self.client.post(reverse("admin_feedback_review", args=[self.good.pk]))
        self.good.refresh_from_db()
        self.assertIsNone(self.good.reviewed_at)

    def test_the_review_button_cannot_send_people_to_another_site(self):
        self.login(self.amina)
        response = self.client.post(reverse("admin_feedback_review", args=[self.good.pk]), {"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("admin_feedback"), fetch_redirect_response=False)

    def test_the_overview_shows_the_average(self):
        self.login(self.amina)
        response = self.client.get(reverse("admin_home"))
        self.assertContains(response, "3.5 / 5")

    def test_an_empty_page_says_so(self):
        Feedback.objects.all().delete()
        self.assertContains(self.page(), "No feedback yet")

    def test_the_page_opens_in_every_language(self):
        for language in ("fr", "ar"):
            choose(self.amina, language)
            self.assertEqual(self.page().status_code, 200)
