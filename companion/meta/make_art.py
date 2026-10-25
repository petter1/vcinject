"""Draw the HOME Menu icon (48x48) and banner (256x128) of vcinject Link."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packaging"))
from make_icon import draw, font  # noqa: E402

here = Path(__file__).parent
icon = draw(label="LINK")
icon.resize((48, 48), Image.LANCZOS).save(here / "icon.png")

banner = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
banner.alpha_composite(icon.resize((112, 112), Image.LANCZOS), (8, 8))
d = ImageDraw.Draw(banner)
d.text((124, 48), "vcinject", font=font(30), fill=(60, 50, 120, 255))
d.text((124, 84), "Link", font=font(30), fill=(118, 92, 206, 255))
banner.save(here / "banner.png")
