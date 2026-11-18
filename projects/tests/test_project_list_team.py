"""The project list shows each project's team (release 1.1): the manager in their column, then the other
members as small overlapping pictures, "+N" after five. Only on wide screens."""

import re

from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from projects.models import Project, ProjectMember

from .base import TeamTestCase


def team_cell(response, project):
    """The Team cell of this project's row in the list (the cell after the manager's)."""
    html = response.content.decode()
    row = re.search(rf'<tr[^>]*>\s*<td><a href="/projects/{project.pk}/">.*?</tr>', html, re.S).group(0)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    return cells[4]  # project, status, priority, manager, team


class TeamColumnTests(TeamTestCase):
    def test_the_column_is_only_on_wide_screens(self):
        self.login(self.sara)
        response = self.client.get("/projects/")
        self.assertContains(response, '<th class="d-none d-lg-table-cell">Team</th>', html=True)
        self.assertContains(response, '<td class="d-none d-lg-table-cell">')

    def test_the_members_besides_the_manager(self):
        self.login(self.sara)
        cell = team_cell(self.client.get("/projects/"), self.project)
        # Sara's initials, with her name when hovering and for screen readers. Omar is the manager: not repeated.
        self.assertIn('title="Sara Ben">SB</span>', cell)
        self.assertIn('<span class="visually-hidden">Sara Ben</span>', cell)
        self.assertNotIn("Omar", cell)

    def test_a_project_with_only_its_manager(self):
        ProjectMember.objects.filter(project=self.project).delete()
        self.login(self.omar)
        cell = team_cell(self.client.get("/projects/"), self.project)
        self.assertIn("Only the manager", cell)

    def test_after_five_people_a_plus_sign_names_the_others(self):
        for name in ["Ali", "Badr", "Dia", "Eva", "Fatou", "Hind"]:
            person = User.objects.create_user(name.lower(), first_name=name)
            ProjectMember.objects.create(project=self.project, user=person)
        self.login(self.omar)
        cell = team_cell(self.client.get("/projects/"), self.project)
        # Seven members by name: Ali, Badr, Dia, Eva, Fatou are shown; Hind and Sara Ben are behind "+2"
        self.assertEqual(cell.count('class="avatar avatar-xs flex-shrink-0"'), 5)
        self.assertIn('title="Hind, Sara Ben">+2</span>', cell)
        self.assertIn("Ali, Badr, Dia, Eva, Fatou, Hind, Sara Ben", cell)  # all of them for screen readers

    def test_a_picture_is_shown_to_teammates(self):
        # Only the picture's name matters here: the address of the picture is built from it
        User.objects.filter(pk=self.sara.pk).update(picture="people/sara.jpg")
        self.login(self.omar)
        cell = team_cell(self.client.get("/projects/"), self.project)
        self.assertIn(f"/people/{self.sara.pk}/picture/?v=sara", cell)

    def test_people_outside_the_project_see_neither_it_nor_its_team(self):
        self.login(self.karim)
        response = self.client.get("/projects/?status=all")
        self.assertNotContains(response, "Firewall")
        self.assertNotContains(response, "Sara Ben")

    def test_the_teams_cost_the_same_number_of_queries_however_many_projects(self):
        self.login(self.amina)

        def queries_for_the_list():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get("/projects/").status_code, 200)
            return len(queries)

        with_one_project = queries_for_the_list()
        for number in range(5):
            project = Project.objects.create(name=f"Project {number}", manager=self.omar)
            ProjectMember.objects.create(project=project, user=self.sara)
            ProjectMember.objects.create(project=project, user=self.karim)
        self.assertEqual(queries_for_the_list(), with_one_project)

    def test_in_arabic(self):
        self.sara.language = "ar"
        self.sara.save()
        self.login(self.sara)
        response = self.client.get("/projects/")
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, '<th class="d-none d-lg-table-cell">الفريق</th>', html=True)
        self.assertIn('<span class="visually-hidden">Sara Ben</span>', team_cell(response, self.project))

    def test_the_administration_list_keeps_six_pictures(self):
        for name in ["Ali", "Badr", "Dia", "Eva", "Fatou", "Hind"]:
            person = User.objects.create_user(name.lower(), first_name=name)
            ProjectMember.objects.create(project=self.project, user=person)
        self.login(self.amina)
        response = self.client.get("/administration/projects/")
        self.assertContains(response, 'title="Sara Ben">+1</span>')
        self.assertContains(response, "Ali, Badr, Dia, Eva, Fatou, Hind, Sara Ben")
