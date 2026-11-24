"""A project's type, partner, budget and cost so far (release 1.3). The type and the partner are seen by everyone who
sees the project; the budget and the cost only by admins, executives, the manager and the co-managers, and changed
by admins and the manager. An ordinary member must never see them, anywhere: pages, exports, Activity."""

import io
import zipfile

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import translation
from openpyxl import load_workbook

from accounts.models import User
from projects.models import Project, ProjectMember
from projects.money import format_mru

from .base import TeamTestCase
from .test_permissions import EveryAddressMixin

BUDGET, COST = 987654321, 123456789  # numbers that appear nowhere else


def marker_forms():
    """Every way the two markers could be written: plain digits, and as each language formats them."""
    forms = set()
    for amount in (BUDGET, COST):
        digits = str(amount)
        forms |= {digits, f"{amount:,}", f"{amount:,}".replace(",", " "), f"{amount:,}".replace(",", " "),
                  f"{amount:,}".replace(",", ".")}
        for language in ("en", "fr", "ar"):
            with translation.override(language):
                forms.add(format_mru(amount))
                forms.add(format_mru(amount).split(" MRU")[0])
    return forms


def page_text(response):
    """What a response holds, as text: the page, or every sheet of an Excel file (unzipped)."""
    body = b"".join(response.streaming_content) if response.streaming else response.content
    if body[:2] == b"PK":  # an .xlsx file is a zip of XML files
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            return "".join(archive.read(name).decode("utf-8", "replace") for name in archive.namelist())
    return body.decode("utf-8", "replace")


class MoneyFormatTests(SimpleTestCase):
    def test_the_same_western_digits_in_every_language(self):
        expected = {"en": "1,250,000 MRU", "fr": "1 250 000 MRU", "ar": "1 250 000 MRU"}
        for language, text in expected.items():
            with translation.override(language):
                self.assertEqual(format_mru(1250000), text)
                self.assertEqual(format_mru(0), "0 MRU")
                self.assertEqual(format_mru(None), "")


class BudgetTestCase(TeamTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Firewall (from TeamTestCase): managed by Omar; Sara in the team (the task is hers); Lina a plain member;
        # Nadia a co-manager; Eva an executive; Karim outside
        cls.lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina")
        cls.nadia = User.objects.create_user("nadia", "nadia@example.com", first_name="Nadia")
        cls.eva = User.objects.create_user("eva", "eva@example.com", first_name="Eva", role=User.Role.EXECUTIVE)
        ProjectMember.objects.create(project=cls.project, user=cls.lina)
        ProjectMember.objects.create(project=cls.project, user=cls.nadia, is_manager=True)

    def set_money(self, budget=BUDGET, cost=COST, **other):
        project = Project.objects.get(pk=self.project.pk)
        project.budget, project.cost = budget, cost
        for name, value in other.items():
            setattr(project, name, value)
        project.save()  # recorded in the history, like a change from a page
        return project


class RuleTests(BudgetTestCase):
    def test_who_sees_and_who_changes_the_money(self):
        project = Project.objects.get(pk=self.project.pk)
        expected = {  # person: (sees, changes)
            self.amina: (True, True), self.eva: (True, False), self.omar: (True, True), self.nadia: (True, False),
            self.sara: (False, False), self.lina: (False, False), self.karim: (False, False)}
        for person, (sees, changes) in expected.items():
            with self.subTest(person=person.username):
                self.assertEqual((project.can_see_budget(person), project.can_change_budget(person)), (sees, changes))

    def test_share_spent_and_over_budget_never_divide_by_zero(self):
        cases = [  # (budget, cost) -> (share spent, over budget)
            ((None, None), (None, False)), ((None, 500), (None, False)), ((0, 500), (None, False)),
            ((0, 0), (None, False)), ((1000, None), (None, False)), ((1000, 0), (0, False)),
            ((1000, 500), (50, False)), ((1000, 1000), (100, False)), ((1000, 1500), (150, True))]
        for (budget, cost), expected in cases:
            with self.subTest(budget=budget, cost=cost):
                project = Project(budget=budget, cost=cost)
                self.assertEqual((project.budget_spent, project.is_over_budget), expected)

    def test_old_projects_are_not_specified_without_money(self):
        project = Project.objects.get(pk=self.project.pk)
        self.assertEqual((project.kind, project.partner, project.budget, project.cost), ("unspecified", "", None, None))


class ProjectPageTests(BudgetTestCase):
    def test_who_sees_the_money_on_the_page_and_the_report(self):
        self.set_money(kind="external", partner="Huawei Mauritanie")
        for person, sees in ((self.amina, True), (self.eva, True), (self.omar, True), (self.nadia, True),
                             (self.sara, False), (self.lina, False)):
            for address in (f"/projects/{self.project.pk}/", f"/projects/{self.project.pk}/report/"):
                with self.subTest(person=person.username, address=address):
                    self.login(person)
                    text = page_text(self.client.get(address))
                    self.assertIn("Huawei Mauritanie", text)  # the partner and the type: everyone
                    self.assertIn("External", text)
                    self.assertEqual('id="budget"' in text, sees)
                    self.assertEqual("987,654,321 MRU" in text, sees)

    def test_spent_and_over_budget(self):
        self.login(self.omar)
        for budget, cost, spent, over in ((1000, 1500, "150%", True), (1000, 250, "25%", False),
                                          (0, 250, None, False), (None, 250, None, False)):
            with self.subTest(budget=budget, cost=cost):
                self.set_money(budget, cost)
                text = page_text(self.client.get(f"/projects/{self.project.pk}/"))
                self.assertEqual('id="spent"' in text, spent is not None)
                if spent:
                    self.assertIn(spent, text)
                self.assertEqual("Over budget" in text, over)

    def test_the_cost_says_when_it_was_updated(self):
        self.set_money(1000, 100)
        self.login(self.omar)
        self.assertContains(self.client.get(f"/projects/{self.project.pk}/"), "updated on ")


class ProjectFormTests(BudgetTestCase):
    def form_data(self, **changes):
        data = {"name": "Firewall", "description": "", "status": "in_progress", "priority": "medium",
                "manager": self.omar.pk, "start_date": "", "end_date": "", "kind": "unspecified", "partner": ""}
        data.update(changes)
        return data

    def edit(self, person, **changes):
        self.login(person)
        return self.client.post(f"/projects/{self.project.pk}/edit/", self.form_data(**changes))

    def test_the_manager_and_admins_change_the_money(self):
        for person, amount in ((self.omar, 5000), (self.amina, 7000)):
            with self.subTest(person=person.username):
                self.assertEqual(self.edit(person, budget=amount, cost=10).status_code, 302)
                self.assertEqual(Project.objects.get(pk=self.project.pk).budget, amount)

    def test_a_co_manager_sees_the_money_locked(self):
        self.set_money(1000, 100)
        self.login(self.nadia)
        form = self.client.get(f"/projects/{self.project.pk}/edit/").context["form"]
        self.assertTrue(form.fields["budget"].disabled and form.fields["cost"].disabled)
        self.edit(self.nadia, budget=1, cost=999999)  # a forged value is ignored
        project = Project.objects.get(pk=self.project.pk)
        self.assertEqual((project.budget, project.cost), (1000, 100))

    def test_members_and_executives_get_no_form(self):
        for person in (self.sara, self.lina, self.eva):
            with self.subTest(person=person.username):
                self.login(person)
                self.assertEqual(self.client.get(f"/projects/{self.project.pk}/edit/").status_code, 403)
                self.assertEqual(self.edit(person, budget=1).status_code, 403)
        self.assertIsNone(Project.objects.get(pk=self.project.pk).budget)

    def test_negative_amounts_are_refused(self):
        response = self.edit(self.omar, budget=-5)
        self.assertEqual(response.status_code, 200)
        self.assertIn("budget", response.context["form"].errors)

    def test_the_partner_only_for_an_external_project(self):
        self.edit(self.omar, kind="external", partner="Mauritel")
        self.assertEqual(Project.objects.get(pk=self.project.pk).partner, "Mauritel")
        self.edit(self.omar, kind="internal", partner="Mauritel")
        self.assertEqual(Project.objects.get(pk=self.project.pk).partner, "")

    def test_a_form_opened_before_the_update_keeps_the_type(self):
        Project.objects.filter(pk=self.project.pk).update(kind="internal")
        data = self.form_data()
        del data["kind"], data["partner"]
        self.login(self.omar)
        self.assertEqual(self.client.post(f"/projects/{self.project.pk}/edit/", data).status_code, 302)
        self.assertEqual(Project.objects.get(pk=self.project.pk).kind, "internal")


class ListExportDashboardTests(BudgetTestCase):
    def test_the_list_columns_only_for_admins_and_executives(self):
        self.set_money()
        for person, sees in ((self.amina, True), (self.eva, True), (self.omar, False), (self.lina, False)):
            with self.subTest(person=person.username):
                self.login(person)
                text = page_text(self.client.get("/projects/?status=all"))
                self.assertEqual(">Budget</th>" in text, sees)
                self.assertEqual("987,654,321" in text, sees)

    def test_the_export(self):
        self.set_money(kind="internal")
        for person, money in ((self.amina, True), (self.eva, True), (self.omar, False), (self.lina, False)):
            with self.subTest(person=person.username):
                self.login(person)
                sheet = load_workbook(io.BytesIO(self.client.get("/projects/export.xlsx").content))["Projects"]
                headers = [cell.value for cell in sheet[1]]
                self.assertIn("Type", headers)
                self.assertEqual("Budget (MRU)" in headers, money)
                row = [cell.value for cell in sheet[2]]
                self.assertIn("Internal", row)
                self.assertEqual(BUDGET in row, money)

    def test_the_dashboard_totals(self):
        self.set_money(1000, 400)
        for person, sees in ((self.amina, True), (self.eva, True), (self.omar, False), (self.sara, False)):
            with self.subTest(person=person.username):
                self.login(person)
                response = self.client.get("/dashboard/")
                labels = [kpi["label"] for kpi in response.context["kpis"]]
                self.assertEqual("Budget of the active projects" in labels, sees)
                if sees:
                    self.assertContains(response, "1,000 MRU")
                    self.assertContains(response, "1 of 1 active project has a budget")


class ActivityTests(BudgetTestCase):
    def activity(self, person):
        self.login(person)
        return page_text(self.client.get(f"/activity/?project={self.project.pk}"))

    def test_a_change_of_the_money_only_is_no_event_for_a_member(self):
        before = self.activity(self.lina).count("updated the project")
        self.set_money(120000, 5000)
        self.assertEqual(self.activity(self.lina).count("updated the project"), before)
        manager_view = self.activity(self.omar)
        self.assertIn("Budget", manager_view)
        self.assertIn("120,000 MRU", manager_view)

    def test_status_and_budget_in_one_save(self):
        self.set_money(BUDGET, COST, status="on_hold")
        member_view = self.activity(self.lina)
        self.assertIn("updated the project", member_view)
        self.assertIn("On hold", member_view)  # the status change is seen...
        for form in marker_forms():
            self.assertNotIn(form, member_view)  # ...and nothing of the budget
        self.assertNotIn("Cost so far", member_view)
        self.assertNotIn(">Budget<", member_view)
        manager_view = self.activity(self.omar)
        self.assertIn("On hold", manager_view)
        self.assertIn("987,654,321 MRU", manager_view)

    def test_a_type_change_is_seen_by_everyone(self):
        self.set_money(None, None, kind="external", partner="Mauritel")
        member_view = self.activity(self.lina)
        self.assertIn("External", member_view)
        self.assertIn("Mauritel", member_view)


class BudgetLeakTests(EveryAddressMixin, TeamTestCase):
    """Every address of the app (the table of test_permissions.py), opened by the two ordinary team members in each
    language: the budget and the cost appear nowhere, in no form of writing, not even inside an Excel file."""

    def test_ordinary_members_never_see_the_money(self):
        project = Project.objects.get(pk=self.project.pk)
        project.budget, project.cost, project.status = BUDGET, COST, "on_hold"
        project.save()  # also a change in the history (Activity)
        forms = marker_forms()
        # The test means something only if the manager does see it
        self.login(self.omar)
        self.assertIn("987,654,321 MRU", page_text(self.client.get(f"/projects/{self.project.pk}/")))

        for language in ("en", "fr", "ar"):
            User.objects.filter(pk__in=[self.sara.pk, self.lina.pk]).update(language=language)
            for person in (self.sara, self.lina):
                self.login(person)
                for name, method, address, _data, _expected in self.rules():
                    if method != "get":
                        continue
                    with self.subTest(language=language, person=person.username, address=name):
                        text = page_text(self.client.get(address))
                        leaks = [form for form in forms if form in text]
                        self.assertEqual(leaks, [])


class MigrationTests(TransactionTestCase):
    """projects/0010 only adds columns: from 0009, every project and history row keeps all its data and gets the type
    "not specified", an empty partner, and no budget or cost."""

    def test_from_0009_to_0010_old_data_is_kept(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("projects", "0009_emailoutbox")])
        old = executor.loader.project_state([("projects", "0009_emailoutbox")]).apps
        # The accounts table is already at its newest shape (only the projects app went back): today's User model
        manager = User.objects.create_user("old-manager")
        project = old.get_model("projects", "Project").objects.create(
            name="Old project", description="Kept as it is", status="in_progress", priority="high", manager_id=manager.pk)
        old.get_model("projects", "HistoricalProject").objects.create(
            id=project.pk, name="Old project", description="Kept as it is", status="planned", priority="high",
            manager_id=manager.pk, created_at=project.created_at, updated_at=project.updated_at,
            history_date=project.created_at, history_type="+")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        after = Project.objects.get(pk=project.pk)
        self.assertEqual((after.name, after.description, after.status, after.priority, after.manager_id),
                         ("Old project", "Kept as it is", "in_progress", "high", manager.pk))
        self.assertEqual((after.kind, after.partner, after.budget, after.cost), ("unspecified", "", None, None))
        version = Project.history.get(id=project.pk)
        self.assertEqual((version.status, version.kind, version.partner, version.budget, version.cost),
                         ("planned", "unspecified", "", None, None))


class LanguageTests(BudgetTestCase):
    def test_french_and_arabic(self):
        self.set_money(1250000, 1500000, kind="external", partner="Mauritel")
        expected = {
            "fr": ("Budget dépassé", "Coût à ce jour", "Externe", "1 250 000 MRU", 'dir="ltr"'),
            "ar": ("تجاوز الميزانية", "التكلفة حتى الآن", "خارجي", "1 250 000 MRU", 'dir="rtl"'),
        }
        for language, texts in expected.items():
            with self.subTest(language=language):
                User.objects.filter(pk=self.omar.pk).update(language=language)
                self.login(self.omar)
                page = page_text(self.client.get(f"/projects/{self.project.pk}/"))
                for text in texts:
                    self.assertIn(text, page)
                # The amount stays left to right inside the page, whatever its direction
                self.assertIn('<span dir="ltr" class="text-nowrap">1 250 000 MRU</span>', page)


class OlderVersionTests(BudgetTestCase):
    """If MTNIMA goes back to 1.2.0 after this migration, that version writes projects without the new columns: the
    database fills them in itself (db_default), so creating and saving projects still works there."""

    def test_a_project_written_without_the_new_columns(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects_project (name, description, status, priority, manager_id, created_at, updated_at)"
                " VALUES ('Written by 1.2.0', '', 'planned', 'medium', %s, now(), now()) RETURNING id", [self.omar.pk])
            pk = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO projects_historicalproject (id, name, description, status, priority, manager_id, created_at,"
                " updated_at, history_date, history_type) VALUES (%s, 'Written by 1.2.0', '', 'planned', 'medium', %s,"
                " now(), now(), now(), '+')", [pk, self.omar.pk])
        project = Project.objects.get(pk=pk)
        self.assertEqual((project.kind, project.partner, project.budget, project.cost), ("unspecified", "", None, None))
        self.assertEqual(Project.history.filter(id=pk).get().kind, "unspecified")
