"""Showing people in the pages: {% avatar person %} and {% person_link person %}.
Load them in a template with {% load people %}.

A picture is only shown to the people who may see this person's profile
(admins, the person, and the people they share a project with: see people_visible_to).
For everyone else, the avatar shows the person's initials, and their name isn't a link."""

from django import template
from django.urls import reverse

from projects.models import people_visible_to

register = template.Library()


def can_see(request, person):
    """True if the person who is logged in may see this person's profile.
    The list of people is read once per page, however many avatars the page shows."""
    if request is None or person is None:
        return False
    viewer = request.user
    if viewer.is_authenticated and (viewer.is_admin or viewer.pk == person.pk):
        return True
    if not hasattr(request, "_people_visible"):
        request._people_visible = set(people_visible_to(viewer).values_list("pk", flat=True))
    return person.pk in request._people_visible


def picture_url(person):
    # The file's name changes with each new picture, so browsers never show an old one
    version = person.picture.name.rsplit("/", 1)[-1].split(".")[0]
    return f"{reverse('person_picture', args=[person.pk])}?v={version}"


@register.inclusion_tag("includes/avatar.html", takes_context=True)
def avatar(context, person, size="sm"):
    """A round picture of the person, or their initials. size: xs, sm, md, lg or xl."""
    show_picture = bool(person and person.picture) and can_see(context.get("request"), person)
    return {
        "person": person,
        "size": size,
        "picture_url": picture_url(person) if show_picture else "",
    }


@register.inclusion_tag("includes/person_link.html", takes_context=True)
def person_link(context, person, css=""):
    """The person's name, as a link to their page when the person who is logged in may see it."""
    url = reverse("person_detail", args=[person.pk]) if can_see(context.get("request"), person) else ""
    return {"person": person, "url": url, "css": css}
