"""Draw the app icon and write it as .png, .icns (macOS) and .ico (Windows)."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def font(size):
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "C:/Windows/Fonts/arialbd.ttf"):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def draw(size=1024, label="INJECT"):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pad = size // 10
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + size // 40, size - pad, size - pad + size // 40),
                                             radius=size // 6, fill=(0, 0, 0, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(size // 40)))
    # cartridge-like body with a vertical gradient
    body = Image.new("RGBA", img.size, (0, 0, 0, 0))
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / size
        grad.putpixel((0, y), (int(118 - 40 * t), int(92 - 30 * t), int(206 - 60 * t), 255))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad, size - pad, size - pad), radius=size // 6, fill=255)
    body.paste(grad.resize(img.size), (0, 0), mask)
    img.alpha_composite(body)
    d = ImageDraw.Draw(img)
    # "screen" with the VC badge
    s0, s1 = size * 0.22, size * 0.78
    d.rounded_rectangle((s0, size * 0.2, s1, size * 0.62), radius=size // 30, fill=(245, 245, 248, 255))
    d.rounded_rectangle((s0 + size * 0.05, size * 0.27, s1 - size * 0.05, size * 0.55),
                        radius=size // 40, fill=(120, 120, 126, 255))
    f = font(int(size * 0.13))
    d.text((size / 2, size * 0.355), "Virtual", font=f, fill="white", anchor="mm")
    d.text((size / 2, size * 0.465), "Console", font=f, fill="white", anchor="mm")
    d.text((size / 2, size * 0.75), label, font=font(int(size * 0.1)), fill=(235, 230, 255, 255),
           anchor="mm")
    return img


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/icon")
    out.mkdir(parents=True, exist_ok=True)
    icon = draw()
    icon.save(out / "vcinject.png")
    icon.resize((256, 256), Image.LANCZOS).save(out / "vcinject-256.png")
    icon.save(out / "vcinject.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(out / "vcinject.icns")
    print(f"Icons written to {out}")
