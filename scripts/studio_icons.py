"""Draws ScriptBot's mark into src/studio/icons/: the letters SB in white on the
app's accent blue (the default colour scheme's button colour, #2563eb), in the
sizes and file names of Speakr's icons under static/img/, which
src/studio/rebrand.py replaces with these when the image is built.

Needs Pillow and the DejaVu fonts, both in the studio image:

    docker run --rm --memory 512m --network none -u "$(id -u):$(id -g)" -v "$PWD":/w -w /w \
        --entrypoint python ghcr.io/blake-dk/speakr-studio:<tag> scripts/studio_icons.py
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

ACCENT = (0x25, 0x63, 0xEB, 255)
WHITE = (255, 255, 255, 255)
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
TEXT = 'SB'
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src', 'studio', 'icons')
SCALE = 4  # drawn larger, then scaled down, for smooth edges


def mark(size, maskable=False):
    big = size * SCALE
    image = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if maskable:
        # Full bleed: the system cuts its own shape; the letters stay inside the safe circle (80 %).
        draw.rectangle((0, 0, big, big), fill=ACCENT)
        text_width = 0.46 * big
    else:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(0.22 * big), fill=ACCENT)
        text_width = 0.64 * big
    font_size = big
    font = ImageFont.truetype(FONT, font_size)
    left, top, right, bottom = font.getbbox(TEXT)
    font_size = int(font_size * text_width / (right - left))
    font = ImageFont.truetype(FONT, font_size)
    left, top, right, bottom = font.getbbox(TEXT)
    x = (big - (right - left)) / 2 - left
    y = (big - (bottom - top)) / 2 - top
    draw.text((x, y), TEXT, font=font, fill=WHITE)
    return image.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, size in (('icon-16x16.png', 16), ('icon-32x32.png', 32), ('icon-64.png', 64), ('icon-128.png', 128),
                       ('icon-180x180.png', 180), ('icon-192x192.png', 192), ('icon-512x512.png', 512)):
        mark(size).save(os.path.join(OUT, name), optimize=True)
    mark(512, maskable=True).save(os.path.join(OUT, 'icon-maskable-512x512.png'), optimize=True)
    # The favicon in the sizes browsers pick from, each drawn at its own size.
    largest = mark(48)
    largest.save(os.path.join(OUT, 'favicon.ico'), sizes=[(16, 16), (32, 32), (48, 48)],
                 append_images=[mark(16), mark(32)])
    print('\n'.join(sorted(os.listdir(OUT))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
