"""Генерирует приветственную картинку в banners/. Запуск: venv\\Scripts\\python tools\\make_banners.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "banners"
W, H = 1280, 640
FONTS = Path("C:/Windows/Fonts")
BOLD, REGULAR, EMOJI = FONTS / "segoeuib.ttf", FONTS / "segoeui.ttf", FONTS / "seguiemj.ttf"

DARK, BRIGHT = (6, 16, 46), (24, 86, 245)
ACCENT, SOFT = (80, 160, 255), (190, 214, 255)

# ключ: (эмодзи, заголовок, подзаголовок)
BANNERS = {
    "start": ("💙", "Xazura Creators", "Продвигай шоп в TikTok\nи получай награду"),
}


def background() -> Image.Image:
    # плавный диагональный градиент: тёмно-синий слева сверху, яркий синий справа снизу
    mask = Image.frombytes("L", (W, H), bytes(
        min(255, int(255 * (x / W * 0.7 + y / H * 0.3) ** 1.3)) for y in range(H) for x in range(W)
    ))
    img = Image.composite(Image.new("RGB", (W, H), BRIGHT), Image.new("RGB", (W, H), DARK), mask)

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((760, 60, 1240, 540), fill=(90, 170, 255, 120))
    g.ellipse((-200, 380, 300, 880), fill=(60, 120, 255, 90))
    g.ellipse((980, -180, 1360, 160), fill=(140, 200, 255, 70))
    img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(90)))

    # тонкие кольца за иконкой
    rings = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    r = ImageDraw.Draw(rings)
    cx, cy = 1000, 320
    for radius, alpha in ((190, 70), (245, 40), (300, 22)):
        r.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(170, 210, 255, alpha), width=3)
    # сетка точек слева сверху
    for x in range(40, 260, 28):
        for y in range(40, 150, 28):
            r.ellipse((x, y, x + 3, y + 3), fill=(170, 210, 255, 35))
    return Image.alpha_composite(img, rings)


def fit_font(path: Path, text: str, size: int, max_width: int, draw: ImageDraw.ImageDraw) -> ImageFont.FreeTypeFont:
    while size > 30:
        font = ImageFont.truetype(str(path), size)
        if draw.multiline_textbbox((0, 0), text, font=font, spacing=6)[2] <= max_width:
            return font
        size -= 4
    return ImageFont.truetype(str(path), size)


def make(key: str, emoji: str, title: str, subtitle: str):
    img = background()

    # полупрозрачная плашка-бренд
    label_font = ImageFont.truetype(str(BOLD), 26)
    label = "XAZURA SHOP"
    pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    p = ImageDraw.Draw(pill)
    lw = p.textlength(label, font=label_font) + 44
    p.rounded_rectangle((80, 92, 80 + lw, 140), radius=24, fill=(255, 255, 255, 28), outline=(170, 210, 255, 110), width=2)
    img = Image.alpha_composite(img, pill)
    d = ImageDraw.Draw(img)
    d.text((102, 99), label, font=label_font, fill=(225, 238, 255))

    title_font = fit_font(BOLD, title, 92, 640, d)
    sub_font = ImageFont.truetype(str(REGULAR), 38)
    title_h = d.multiline_textbbox((0, 0), title, font=title_font, spacing=6)[3]
    sub_h = d.multiline_textbbox((0, 0), subtitle, font=sub_font, spacing=8)[3]
    top = 175 + (330 - (title_h + 40 + sub_h)) // 2

    d.multiline_text((80, top), title, font=title_font, fill="white", spacing=6)
    bar_y = top + title_h + 20
    d.rounded_rectangle((82, bar_y, 162, bar_y + 6), radius=3, fill=ACCENT)
    d.multiline_text((80, bar_y + 24), subtitle, font=sub_font, fill=SOFT, spacing=8)

    d.text((80, 560), "@XazuraShop", font=ImageFont.truetype(str(BOLD), 28), fill=(150, 190, 255))

    # иконка с мягкой тенью
    emoji_font = ImageFont.truetype(str(EMOJI), 220)
    icon = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(icon).text((1000, 320), emoji, font=emoji_font, embedded_color=True, anchor="mm")
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.paste((0, 10, 40, 140), mask=icon.getchannel("A"))
    shadow = shadow.transform((W, H), Image.AFFINE, (1, 0, 0, 0, 1, -18)).filter(ImageFilter.GaussianBlur(18))
    img = Image.alpha_composite(img, shadow)
    img = Image.alpha_composite(img, icon)

    img.convert("RGB").save(OUT / f"{key}.jpg", quality=92)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for key, (emoji, title, subtitle) in BANNERS.items():
        make(key, emoji, title, subtitle)
        print("ok", key)
