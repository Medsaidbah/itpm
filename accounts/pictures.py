"""Profile pictures: every uploaded picture is turned into a small square JPEG.

Saving the picture again, instead of keeping the uploaded file, has three advantages:
- the files stay small (a phone photo of 5 MB becomes about 15 KB);
- everything hidden in the original file is dropped, like the GPS position of a phone photo;
- the app only ever gives out files it made itself, never a file exactly as someone uploaded it.
"""

from io import BytesIO
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

PICTURE_SIZE = 256  # pixels, width and height
MAX_PICTURE_MB = 5
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


def make_profile_picture(uploaded_file):
    """The uploaded picture, turned upright, cut to a square, made small and saved as JPEG.
    Raises ValidationError when the file isn't a picture we can use."""
    if uploaded_file.size > MAX_PICTURE_MB * 1024 * 1024:
        raise ValidationError(f"This picture is too big: pictures can be up to {MAX_PICTURE_MB} MB.")
    uploaded_file.seek(0)
    try:
        image = Image.open(uploaded_file)
        image_format = image.format
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError("This file isn't a picture we can read. Use a JPG or PNG picture.")
    if image_format not in ACCEPTED_FORMATS:
        raise ValidationError("Use a JPG, PNG, WEBP or GIF picture.")

    image = ImageOps.exif_transpose(image)  # phone photos say how they were held: turn them upright
    if image.mode != "RGB":
        # Transparent parts (PNG, GIF) become white, because JPEG has no transparency
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, "white")
        image.paste(rgba, mask=rgba.getchannel("A"))
    image = ImageOps.fit(image, (PICTURE_SIZE, PICTURE_SIZE), Image.Resampling.LANCZOS)  # the middle square

    output = BytesIO()
    image.save(output, "JPEG", quality=85, optimize=True)
    # A random name: the file name tells nothing about the person
    return ContentFile(output.getvalue(), name=f"{uuid4().hex}.jpg")
