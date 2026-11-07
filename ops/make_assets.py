"""Builds the site's image assets from the two source photos.

Usage: python3 ops/make_assets.py <ham-negroni.jpg> <founders.jpg> <out-dir>
Produces: coaster.jpg, logo.png, favicon.png, apple-touch-icon.png, hero.jpg,
          founders.jpg, og.jpg
"""
import sys
from PIL import Image, ImageDraw, ImageFont, ImageOps

ham_path, founders_path, out = sys.argv[1], sys.argv[2], sys.argv[3].rstrip("/")

PINK = (246, 215, 208)
CREAM = (255, 247, 238)
ESPRESSO = (43, 24, 18)
NEGRONI = (200, 55, 30)


def load(p):
    im = Image.open(p)
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


ham = load(ham_path)
W, H = ham.size  # 1108 x 1477

# --- square crop around the glass + plate (full width, lower part of frame)
side = W
top = H - side  # 369
sq = ham.crop((0, top, side, top + side))

# coaster.jpg: big square used as a round image in the page (CSS border-radius)
sq.resize((1000, 1000), Image.LANCZOS).save(f"{out}/coaster.jpg", quality=84, optimize=True, progressive=True)


def circle(img, size, ring=0, ring_color=CREAM, bg=None):
    im = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0) if bg is None else bg + (255,))
    if ring:
        ringimg = Image.new("RGBA", (size, size), ring_color + (255,))
        base.paste(ringimg, (0, 0), mask)
        inner = size - 2 * ring
        im2 = im.resize((inner, inner), Image.LANCZOS)
        m2 = Image.new("L", (inner * 4, inner * 4), 0)
        ImageDraw.Draw(m2).ellipse((0, 0, inner * 4 - 1, inner * 4 - 1), fill=255)
        m2 = m2.resize((inner, inner), Image.LANCZOS)
        base.paste(im2, (ring, ring), m2)
    else:
        base.paste(im, (0, 0), mask)
    return base


circle(sq, 512).save(f"{out}/logo.png", optimize=True)
circle(sq, 64).save(f"{out}/favicon.png", optimize=True)
circle(sq, 180, ring=10, ring_color=PINK, bg=PINK).convert("RGB").save(f"{out}/apple-touch-icon.png", optimize=True)

# hero.jpg: the full photo, web size
ham.resize((900, round(H * 900 / W)), Image.LANCZOS).save(f"{out}/hero.jpg", quality=82, optimize=True, progressive=True)

# founders.jpg
f = load(founders_path)
fw, fh = f.size
f.resize((1400, round(fh * 1400 / fw)), Image.LANCZOS).save(f"{out}/founders.jpg", quality=82, optimize=True, progressive=True)

# og.jpg 1200x630: pink card, round photo left, wordmark right
og = Image.new("RGB", (1200, 630), PINK)
d = ImageDraw.Draw(og)
disc = circle(sq, 470, ring=14, ring_color=CREAM)
og.paste(disc, (70, 80), disc)
try:
    title_font = ImageFont.truetype("/usr/share/fonts/truetype/google-fonts/Lora-Variable.ttf", 92)
    title_font.set_variation_by_axes([700])
    sub_font = ImageFont.truetype("/usr/share/fonts/opentype/inter/Inter-Medium.otf", 34)
    tiny_font = ImageFont.truetype("/usr/share/fonts/opentype/inter/Inter-SemiBold.otf", 26)
except Exception:
    title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 84)
    sub_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    tiny_font = sub_font
x = 590
d.text((x, 150), "Ham &", font=title_font, fill=ESPRESSO)
d.text((x, 250), "Negroni", font=title_font, fill=NEGRONI)
d.text((x, 392), "Dating for singles in Japan", font=sub_font, fill=ESPRESSO)
d.text((x, 438), "who'd rather meet over a drink.", font=sub_font, fill=ESPRESSO)
d.text((x, 520), "hamandnegroni  ·  members only", font=tiny_font, fill=(120, 70, 60))
og.save(f"{out}/og.jpg", quality=86, optimize=True)
print("ok")
