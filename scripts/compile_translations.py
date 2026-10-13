"""Turns the translation files you can read and edit (.po) into the files the app reads (.mo).

When to run it: after you change a text in
    locale/fr/LC_MESSAGES/django.po     (French)
    locale/ar/LC_MESSAGES/django.po     (Arabic)
From the project folder, with the virtual environment on:
    python scripts/compile_translations.py
then restart the app. It needs nothing but Python: no extra program to install.

How a .po file looks, message by message:
    msgid "Log out"             <- the English text in the app. Never change this line.
    msgstr "Se déconnecter"     <- the text people read in this language. Change this one.
Some messages have a singular and a plural form (msgstr[0], msgstr[1]...). Keep the %(name)s and %(count)d
signs as they are: the app puts a person's name or a number there. This script stops, and changes
nothing, when a sign is wrong or missing (a wrong one would break the page).

The automatic test "the compiled files match the .po files" (projects/tests/test_languages.py) fails
when you edit a .po file and forget to run this script.
"""

import re
import struct
import sys
from pathlib import Path

HEADER_OF_A_MO_FILE = 0x950412DE  # the number every .mo file starts with
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
# The signs a text can contain for the app to fill in: %(name)s, %(count)d, %s ... ("%%" is a plain % sign)
SIGN = re.compile(r"%(?:\((\w+)\))?[-#0 +]*\d*(?:\.\d+)?([sdifrx%])")


class TranslationError(Exception):
    """Something is wrong in a .po file. The message says what and where."""


def unquote(line):
    """What is between the quotes of a .po line: '"Say \\"hi\\"\\n"' becomes 'Say "hi"' and a new line."""
    line = line.strip()
    if len(line) < 2 or line[0] != '"' or line[-1] != '"':
        raise TranslationError(f"expected text between quotes: {line!r}")
    return re.sub(r"\\(.)", lambda match: ESCAPES.get(match.group(1), match.group(0)), line[1:-1])


def signs(text):
    """The signs of a text, like {'name:s', 'count:d'}."""
    return {f"{name}:{kind}" for name, kind in SIGN.findall(text) if kind != "%"}


class Message:
    """One message of a .po file, while it is being read."""

    def __init__(self):
        self.context = self.original = self.plural = None
        self.translations = {}  # 0 -> text; for plural messages also 1, 2...
        self.fuzzy = False  # "to check": the app doesn't use these (same as the usual tools)
        self.python_format = False
        self.line = 0

    def key_and_text(self):
        """How the .mo file stores it: the key (context, original, plural form) and the translation."""
        key = self.original if self.plural is None else f"{self.original}\0{self.plural}"
        if self.context is not None:
            key = f"{self.context}\x04{key}"
        return key, "\0".join(self.translations[number] for number in sorted(self.translations))

    def check(self, where):
        """Every sign in a translation must also be in the English text."""
        english = signs(self.original) | signs(self.plural or "")
        for text in self.translations.values():
            unknown = signs(text) - english
            if unknown:
                raise TranslationError(
                    f"{where}, line {self.line}: the text {text!r} uses {sorted(unknown)}, "
                    f"which is not in the English text {self.original!r}"
                )


def read_po(path, missing=None):
    """All the messages of a .po file that have a translation: {key: translation}.
    If you give a list as "missing", the English texts that still have no translation are added to it."""
    finished = {}
    message = Message()
    field = None  # the part of the message the next lines in quotes continue

    def finish():
        nonlocal message, field
        translated = any(message.translations.values())
        if missing is not None and message.original and (message.fuzzy or not translated):
            missing.append(message.original)
        if message.original is not None and not message.fuzzy and (translated or message.original == ""):
            if message.python_format:
                message.check(path)
            key, text = message.key_and_text()
            finished[key] = text
        message, field = Message(), None

    # Cut at the end of lines only (not at the other, rare "line separator" characters a text could contain)
    lines = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if line.startswith("#~"):
            continue  # an old message the app doesn't use anymore
        if not line:
            finish()
        elif line.startswith("#,"):
            flags = [flag.strip() for flag in line[2:].split(",")]
            message.fuzzy = message.fuzzy or "fuzzy" in flags
            message.python_format = message.python_format or "python-format" in flags
        elif line.startswith("#"):
            continue  # a comment (where the text comes from...)
        elif line.startswith('"'):
            if field is None:
                raise TranslationError(f"{path}, line {number}: text between quotes without a msgid before it")
            text = unquote(line)
            if field == "context":
                message.context += text
            elif field == "original":
                message.original += text
            elif field == "plural":
                message.plural += text
            else:
                message.translations[field] += text
        else:
            word, _separator, rest = line.partition(" ")
            if word in ("msgctxt", "msgid") and message.translations:
                finish()  # a new message starts right after the previous one
            if not message.line:
                message.line = number
            if word == "msgctxt":
                message.context, field = unquote(rest), "context"
            elif word == "msgid":
                message.original, field = unquote(rest), "original"
            elif word == "msgid_plural":
                message.plural, field = unquote(rest), "plural"
            elif word == "msgstr":
                message.translations[0], field = unquote(rest), 0
            elif word.startswith("msgstr[") and word.endswith("]") and word[7:-1].isdigit():
                field = int(word[7:-1])
                message.translations[field] = unquote(rest)
            else:
                raise TranslationError(f"{path}, line {number}: I don't understand this line: {line[:60]!r}")
    finish()
    return finished


def write_mo(messages, path):
    """Writes the .mo file: a binary list of texts and translations, sorted so the app finds them fast."""
    keys = sorted(messages, key=lambda key: key.encode("utf-8"))
    originals, translations, positions = b"", b"", []
    for key in keys:
        original, translated = key.encode("utf-8"), messages[key].encode("utf-8")
        positions.append((len(originals), len(original), len(translations), len(translated)))
        originals += original + b"\0"  # each text ends with a 0 byte, which isn't counted in its length
        translations += translated + b"\0"
    count = len(keys)
    # After the 7 numbers of the header come two lists of (length, place): the English texts, then the translations
    first_original = 7 * 4 + 16 * count
    first_translation = first_original + len(originals)
    original_list, translation_list = [], []
    for place_o, length_o, place_t, length_t in positions:
        original_list += [length_o, place_o + first_original]
        translation_list += [length_t, place_t + first_translation]
    data = struct.pack("<7I", HEADER_OF_A_MO_FILE, 0, count, 7 * 4, 7 * 4 + count * 8, 0, 0)
    data += struct.pack(f"<{len(original_list)}I", *original_list)
    data += struct.pack(f"<{len(translation_list)}I", *translation_list)
    Path(path).write_bytes(data + originals + translations)


def compile_po(po_path, mo_path):
    """Reads a .po file and writes the .mo file next to it. Returns the number of messages."""
    messages = read_po(po_path)
    # The usual tool (msgfmt) leaves the date the file was created out of the .mo file: so do we
    header = messages.get("", "")
    kept = [line for line in header.splitlines(keepends=True) if not line.startswith("POT-Creation-Date:")]
    messages[""] = "".join(kept)
    write_mo(messages, mo_path)
    return len(messages) - 1  # without the header (the empty text at the start)


def main(arguments):
    project_folder = Path(__file__).resolve().parent.parent  # the folder with manage.py
    paths = [Path(argument) for argument in arguments]
    if not paths:
        paths = sorted((project_folder / "locale").glob("*/LC_MESSAGES/django.po"))
    if not paths:
        print("No translation file found (locale/<language>/LC_MESSAGES/django.po).")
        return 1
    try:
        # Read everything first: if one file is wrong, no .mo file is touched
        for path in paths:
            read_po(path)
        for path in paths:
            missing = []
            read_po(path, missing)
            count = compile_po(path, path.with_suffix(".mo"))
            print(f"{path.with_suffix('.mo')}: {count} messages")
            if missing:
                print(f"  Careful: {len(missing)} texts have no translation yet (people see them in English), "
                      f"like {missing[0]!r}")
    except TranslationError as error:
        print(f"Nothing was changed. Please fix this first:\n  {error}")
        return 1
    print("Done. Restart the app to see the changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
