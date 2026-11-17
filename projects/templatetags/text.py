"""Text people typed (comments, descriptions), shown in the pages with clickable links:
    {{ comment.body|linkify|linebreaksbr }}
Load it in a template with {% load text %}.

Only web addresses (http://, https://, www.) and email addresses become links. Everything else stays plain
text: what people typed is escaped first, so a comment can't add its own HTML or a "javascript:" link."""

import re

from django import template
from django.utils.html import Urlizer
from django.utils.safestring import mark_safe

register = template.Library()

# A long address is shortened on the page (the link itself still goes to the whole address)
LINK_TEXT_LIMIT = 70


class LinkFinder(Urlizer):
    """Django's own link finder, which also knows that the Arabic comma, semicolon and question mark
    end a sentence: "انظر https://example.org، ثم" links to https://example.org, without the comma."""

    trailing_punctuation_chars = Urlizer.trailing_punctuation_chars + "،؛؟"


find_links = LinkFinder()

# The links LinkFinder writes start exactly like this (what people typed is escaped, so it can't)
LINK_START = re.compile(r'<a href="')
WEB_LINK_START = re.compile(r'<a dir="ltr" href="(?=https?://)')


@register.filter(is_safe=True)
def linkify(text):
    html = find_links(text, trim_url_limit=LINK_TEXT_LIMIT, autoescape=True)
    # dir="ltr": an address reads left to right, even in an Arabic sentence
    html = LINK_START.sub('<a dir="ltr" href="', html)
    # Web pages open in a new tab, so the task stays open. noopener: the other site can't touch this tab;
    # noreferrer: it isn't told which page of the app the link was on.
    html = WEB_LINK_START.sub('<a dir="ltr" target="_blank" rel="noopener noreferrer" href="', html)
    return mark_safe(html)
