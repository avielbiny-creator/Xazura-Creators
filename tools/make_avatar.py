"""Аватарка бота в стиле баннеров. Запуск: venv\\Scripts\\python tools\\make_avatar.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "banners" / "avatar.png"
S = 1024  # Telegram показывает аватарку кругом: всё важное держим в центре
FONTS = Path("C:/Windows/Fonts")
BOLD, EMOJI = FONTS / "segoeuib.ttf", FONTS / "seguiemj.ttf"
DARK, BRIGHT = (6, 16, 46), (24, 86, 245)


def background() -> Image.Image:
    # диагональный градиент от тёмно-синего к яркому
    mask = Image.frombytes("L", (S, S), bytes(
        min(255, int(255 * ((x + y) / (2 * S)) ** 1.2)) for y in range(S) for x in range(S)
    ))
    img = Image.composite(Image.new("RGB", (S, S), BRIGHT), Image.new("RGB", (S, S), DARK), mask).convert("RGBA")
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((S * 0.18, S * 0.12, S * 0.82, S * 0.76), fill=(90, 170, 255, 130))
    g.ellipse((S * 0.55, S * 0.6, S * 1.1, S * 1.15), fill=(140, 200, 255, 70))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(110)))
    rings = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    r = ImageDraw.Draw(rings)
    c = S // 2
    for radius, alpha in ((300, 70), (360, 40), (420, 22)):
        r.ellipse((c - radius, c - radius - 40, c + radius, c + radius - 40), outline=(170, 210, 255, alpha), width=5)
    return Image.alpha_composite(img, rings)


def main():
    img = background()

    # Большая «X» с мягкой тенью
    x_font = ImageFont.truetype(str(BOLD), 520)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((S // 2, S // 2 - 70), "X", font=x_font, fill="white", anchor="mm")
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow.paste((0, 10, 40, 150), mask=layer.getchannel("A"))
    shadow = shadow.transform((S, S), Image.AFFINE, (1, 0, 0, 0, 1, -20)).filter(ImageFilter.GaussianBlur(22))
    img = Image.alpha_composite(img, shadow)
    img = Image.alpha_composite(img, layer)

    # Значок денег в углу буквы
    emoji = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(emoji).text((S // 2 + 215, S // 2 - 255), "💸", font=ImageFont.truetype(str(EMOJI), 150),
                               embedded_color=True, anchor="mm")
    img = Image.alpha_composite(img, emoji)

    # Подпись-плашка
    d = ImageDraw.Draw(img)
    label_font = ImageFont.truetype(str(BOLD), 68)
    label = "CREATORS"
    w = d.textlength(label, font=label_font)
    pill = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    p = ImageDraw.Draw(pill)
    top = S // 2 + 230
    p.rounded_rectangle(((S - w) / 2 - 44, top, (S + w) / 2 + 44, top + 104), radius=52,
                        fill=(255, 255, 255, 38), outline=(190, 220, 255, 150), width=4)
    img = Image.alpha_composite(img, pill)
    ImageDraw.Draw(img).text((S // 2, top + 52), label, font=label_font, fill=(235, 244, 255), anchor="mm")

    img.convert("RGB").save(OUT)
    print("ok", OUT)


if __name__ == "__main__":
    main()
