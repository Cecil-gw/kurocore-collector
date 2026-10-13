import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


THUMB_SIZE = 200
COLS = 6
LABEL_H = 24
BG = (13, 17, 23)
TXT = (139, 148, 158)


def collect_images(root: str) -> list[Path]:
    root_path = Path(root)
    images = []
    for illust_dir in sorted(root_path.iterdir()):
        if not illust_dir.is_dir():
            continue
        for img in sorted(illust_dir.glob("*.jpg")):
            images.append(img)
    return images


def generate(root: str, output: str, cols: int = COLS, thumb: int = THUMB_SIZE):
    images = collect_images(root)
    if not images:
        print("No images found.")
        return

    rows = (len(images) + cols - 1) // cols
    cell_w = thumb + 8
    cell_h = thumb + LABEL_H + 8
    width = cols * cell_w + 8
    height = rows * cell_h + 8

    sheet = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("consola.ttf", 11)
    except Exception:
        font = ImageFont.load_default()

    for i, img_path in enumerate(images):
        row, col = divmod(i, cols)
        x = 8 + col * cell_w
        y = 8 + row * cell_h

        try:
            img = Image.open(img_path)
            img.thumbnail((thumb, thumb), Image.LANCZOS)
            ix = x + (thumb - img.width) // 2
            iy = y + (thumb - img.height) // 2
            sheet.paste(img, (ix, iy))
        except Exception:
            draw.rectangle([x, y, x + thumb, y + thumb], fill=(33, 38, 45))

        label = f"#{img_path.parent.name.split('_')[0]}"
        draw.text((x, y + thumb + 4), label, fill=TXT, font=font)

    sheet.save(output, quality=90)
    print(f"Contact sheet: {output}")
    print(f"  {len(images)} images, {cols} cols, {rows} rows")
    print(f"  Size: {width}x{height}px")


if __name__ == "__main__":
    gallery = "gallery"
    out = "contact_sheet.png"
    c = COLS
    if len(sys.argv) > 1:
        gallery = sys.argv[1]
    if len(sys.argv) > 2:
        out = sys.argv[2]
    if len(sys.argv) > 3:
        c = int(sys.argv[3])
    generate(gallery, out, c)
