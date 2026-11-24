"""{{ project.budget|mru }}: an amount in MRU (projects/money.py), kept left to right inside Arabic text.
Load it in a template with {% load money %}."""

from django import template
from django.utils.html import format_html

from projects.money import format_mru

register = template.Library()


@register.filter
def mru(amount):
    if amount is None:
        return ""
    return format_html('<span dir="ltr" class="text-nowrap">{}</span>', format_mru(amount))
