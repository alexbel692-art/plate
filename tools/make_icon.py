"""Рисует значок программы (номерной знак) -> resources/icon.ico."""
import pathlib

from PIL import Image, ImageDraw, ImageFont

out = pathlib.Path(__file__).resolve().parent.parent / "resources"
out.mkdir(exist_ok=True)
S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 60, 248, 196), radius=24, fill=(31, 111, 235, 255))
d.rounded_rectangle((22, 74, 234, 182), radius=14, fill=(255, 255, 255, 255))
try:
    font = ImageFont.truetype("arialbd.ttf", 74)
except OSError:
    font = ImageFont.load_default()
d.text((128, 128), "A77", fill=(20, 20, 20, 255), font=font, anchor="mm")
img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon ok")
