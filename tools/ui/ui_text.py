"""Text and vector layer of the screen background, and the PL glyph atlas (PIL only).

Run with the macOS system Python, the one with Pillow (the project venv has
numpy/OpenCV but no PIL):
    /usr/bin/python3 -E tools/ui/ui_text.py job.json        (-I would hide the user-site Pillow)
job.json (written by build_ui.py):
  {"w":800, "h":480, "out":"layer.png", "atlas":"atlas.png", "cell":[12,24],
   "glyphs":"...", "ops":[{"op":"rect", "xy":[x0,y0,x1,y1], "fill":"#rrggbb",
   "outline":"#rrggbb", "r":6}, {"op":"text", "xy":[x,y], "s":"...", "size":14,
   "font":"cjk"|"mono", "fill":"#rrggbb", "anchor":"la"}, ...]}
layer.png is RGBA over transparent; atlas.png is one 12x24 cell per glyph, in order.
"""
import json
import sys

from PIL import Image, ImageDraw, ImageFont

FONTS = {
    "cjk":      ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    "cjk_bold": ("/System/Library/Fonts/Hiragino Sans GB.ttc", 1),
    "mono":     ("/System/Library/Fonts/Menlo.ttc", 1),      # Menlo Bold: has ° Δ ● ▲ ↑ ↓ ±
    "mono_reg": ("/System/Library/Fonts/Menlo.ttc", 0),
}
_cache = {}


def font(kind, size):
    key = (kind, size)
    if key not in _cache:
        path, idx = FONTS[kind]
        _cache[key] = ImageFont.truetype(path, size, index=idx)
    return _cache[key]


def draw_layer(job):
    im = Image.new("RGBA", (job["w"], job["h"]), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for op in job["ops"]:
        if op["op"] == "rect":
            d.rounded_rectangle(op["xy"], radius=op.get("r", 0), fill=op.get("fill"),
                                outline=op.get("outline"), width=op.get("width", 1))
        elif op["op"] == "line":
            d.line(op["xy"], fill=op["fill"], width=op.get("width", 1))
        elif op["op"] == "text":
            d.text(op["xy"], op["s"], font=font(op.get("font", "cjk"), op["size"]),
                   fill=op["fill"], anchor=op.get("anchor", "la"))
    im.save(job["out"])


def draw_atlas(job):
    cw, ch = job["cell"]
    glyphs = job["glyphs"]
    f = font("mono", job.get("glyph_size", 18))
    asc, desc = f.getmetrics()
    im = Image.new("L", (cw * len(glyphs), ch), 0)
    d = ImageDraw.Draw(im)
    for i, g in enumerate(glyphs):
        w = f.getlength(g)
        d.text((i * cw + (cw - w) / 2, (ch - asc - desc) / 2), g, font=f, fill=255)
    im.save(job["atlas"])


if __name__ == "__main__":
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    draw_layer(job)
    if job.get("atlas"):
        draw_atlas(job)
    print("ui_text: wrote", job["out"], job.get("atlas", ""))
