"""Draws ScriptBot's icons into src/studio/icons/: Noto Emoji's clapperboard
(src/studio/icons/noto-emoji/, with its source and licence), with a thin white
outline so that its dark board shows on a dark tab or top bar, in the sizes and
file names of Speakr's icons under static/img/, which src/studio/rebrand.py
replaces with these when the image is built. One image for every size and
device: the icons do not depend on the system's emoji font.

Needs Pillow, which the studio image has:

    docker run --rm --memory 512m --network none -u "$(id -u):$(id -g)" -v "$PWD":/w -w /w \
        --entrypoint python ghcr.io/blake-dk/speakr-studio:<tag> -I scripts/studio_icons.py
"""

import os
import sys

from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'src', 'studio', 'icons', 'noto-emoji', 'emoji_u1f3ac.png')
OUT = os.path.join(ROOT, 'src', 'studio', 'icons')
WHITE = (255, 255, 255)
BASE = 512        # the outlined mark is made once at this size, then scaled down
INSET = 24        # room for the outline inside the square
OUTLINE = 21      # outline width at 512 px: about 1 px at 16 px


def outlined_mark():
    source = Image.open(SOURCE).convert('RGBA')
    inner = source.resize((BASE - 2 * INSET, BASE - 2 * INSET), Image.LANCZOS)
    mark = Image.new('RGBA', (BASE, BASE), (0, 0, 0, 0))
    mark.alpha_composite(inner, (INSET, INSET))
    halo = Image.new('RGBA', (BASE, BASE), WHITE + (0,))
    halo.putalpha(mark.split()[3].filter(ImageFilter.MaxFilter(2 * OUTLINE + 1)))
    out = Image.new('RGBA', (BASE, BASE), (0, 0, 0, 0))
    out.alpha_composite(halo)
    out.alpha_composite(mark)
    return out


def main():
    mark = outlined_mark()
    for name, size in (('icon-16x16.png', 16), ('icon-32x32.png', 32), ('icon-64.png', 64), ('icon-128.png', 128),
                       ('icon-180x180.png', 180), ('icon-192x192.png', 192), ('icon-512x512.png', 512)):
        mark.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, name), optimize=True)
    # Maskable: the system cuts its own shape out of a full square, so the mark sits on white,
    # inside the safe circle (80 % of the side).
    maskable = Image.new('RGBA', (BASE, BASE), WHITE + (255,))
    side = int(BASE * 0.56)
    maskable.alpha_composite(mark.resize((side, side), Image.LANCZOS), ((BASE - side) // 2, (BASE - side) // 2))
    maskable.save(os.path.join(OUT, 'icon-maskable-512x512.png'), optimize=True)
    # The favicon in the sizes browsers pick from, each scaled from the same mark.
    mark.resize((48, 48), Image.LANCZOS).save(
        os.path.join(OUT, 'favicon.ico'), sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[mark.resize((16, 16), Image.LANCZOS), mark.resize((32, 32), Image.LANCZOS)])
    print('\n'.join(sorted(n for n in os.listdir(OUT) if not os.path.isdir(os.path.join(OUT, n)))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
