"""Showing task files inside the browser (release 1.1, see docs/ROADMAP.md): PDFs in the browser's own PDF viewer,
pictures as thumbnails on the task's page.

Only these five kinds of file are ever shown, and the kind is read from the first bytes of the file itself,
never from its name: an HTML page named "photo.png" is not a picture, and a real PDF named "notes.txt" is a PDF.
Everything else (SVG pictures, web pages, Office files...) is only ever downloaded, as before: an SVG or HTML
file shown inside the app could run its own scripts in it.

A preview is sent with strict headers (see PREVIEW_HEADERS): its exact type, nosniff (the browser may not guess
another type), and a Content-Security-Policy that sandboxes it with nothing allowed to load. That policy is the
one GitHub uses for raw files; Edge 157 was checked to show PDFs and pictures with it."""

# (what the file starts with, its type). WebP files start with "RIFF", 4 bytes of size, then "WEBP".
SIGNATURES = [
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]
HEAD_SIZE = 12  # the bytes needed to recognise every kind above

PDF = "application/pdf"

PREVIEW_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # Sandboxed, nothing may load; inline styles only so the browser can centre a picture opened on its own
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    # Other sites may not show our files in their pages
    "Cross-Origin-Resource-Policy": "same-origin",
    # Shared caches (proxies) may not keep a copy: the files are only for the task's team
    "Cache-Control": "private",
}


def preview_type(head):
    """The type of a file that can be shown in the browser, from its first bytes, or None."""
    for signature, content_type in SIGNATURES:
        if head.startswith(signature):
            return content_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None
