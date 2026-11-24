"""Amounts of money (release 1.3): whole MRU only, written with the same Western digits as the rest of the app.
English groups the thousands with a comma (1,250,000 MRU); French and Arabic with a no-break space (1 250 000 MRU),
like the French writing used in Mauritania: a space can't be mistaken for a decimal mark."""

from django.utils.translation import get_language

CURRENCY = "MRU"


def format_mru(amount):
    """1250000 -> "1,250,000 MRU" (English) or "1 250 000 MRU" (French, Arabic); "" for no amount."""
    if amount is None:
        return ""
    separator = "," if (get_language() or "en").startswith("en") else " "
    return f"{amount:,}".replace(",", separator) + " " + CURRENCY
