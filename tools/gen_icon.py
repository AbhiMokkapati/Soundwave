from PIL import Image, ImageDraw

SIZE = 256
BG = (10, 16, 24, 255)
ACCENT = (41, 224, 255, 255)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

radius = 56
draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=radius, fill=BG)

bars = [
    (30, 96, 158, 210),
    (86, 46, 214, 234),
    (142, 118, 270, 198),
    (198, 68, 326, 222),
]
bar_w = 34
scale = SIZE / 340
for x, top, _x2, bottom in bars:
    x0 = x * scale
    x1 = x0 + bar_w * scale
    y0 = top * scale
    y1 = bottom * scale
    draw.rounded_rectangle([x0, y0, x1, y1], radius=bar_w * scale / 2.2, fill=ACCENT)

out_path = "webapp/static/icon.ico"
img.save(out_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out_path)
