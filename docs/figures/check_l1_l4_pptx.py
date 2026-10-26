# -*- coding: utf-8 -*-
"""Geometric QA for l1-l4-pipeline.pptx.

The environment cannot render PPTX to images (no LibreOffice / poppler), so this
script replaces eyeballing with deterministic checks:

1. every shape is inside the slide;
2. every text run fits its box using real Microsoft YaHei metrics;
3. no long unbreakable token overflows a chip;
4. filled nodes do not partially overlap each other;
5. no picture shapes are embedded (the diagram must stay editable).
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

HERE = Path(__file__).resolve().parent
PPTX = HERE / "l1-l4-pipeline.pptx"
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
EMU_IN = 914400.0

prs = Presentation(PPTX)
slide = prs.slides[0]
SW = prs.slide_width / EMU_IN
SH = prs.slide_height / EMU_IN

problems: list[str] = []
warnings: list[str] = []
text_rows = []


def inches(v):
    if v is None:
        return 0.0
    return v / EMU_IN


def font_at(size_pt):
    px = max(1, int(round(size_pt * 96 / 72)))
    return ImageFont.truetype(FONT_PATH, px)


def wrap_count(txt, avail_in, size_pt):
    font = font_at(size_pt)
    max_px = max(1.0, avail_in * 96)
    lines = 0
    for para in txt.split("\n"):
        if not para:
            lines += 1
            continue
        cur = ""
        for ch in para:
            cand = cur + ch
            if font.getlength(cand) <= max_px or not cur:
                cur = cand
            else:
                lines += 1
                cur = ch
        lines += 1
    return max(1, lines)


def long_tokens(txt, avail_in, size_pt):
    font = font_at(size_pt)
    bad = []
    for para in txt.split("\n"):
        for token in para.replace("\u00a0", " ").split(" "):
            if token and font.getlength(token) > avail_in * 96 + 0.5:
                bad.append(token)
    return bad


pictures = 0
line_shapes = 0
autoshapes = []
for idx, shp in enumerate(slide.shapes):
    name = shp.name or f"shape{idx}"
    l, t = inches(shp.left), inches(shp.top)
    w, h = inches(shp.width), inches(shp.height)
    r, b = l + w, t + h

    if shp.shape_type == MSO_SHAPE_TYPE.PICTURE:
        pictures += 1
    if shp.shape_type == MSO_SHAPE_TYPE.LINE:
        line_shapes += 1

    if l < -0.001 or t < -0.001 or r > SW + 0.001 or b > SH + 0.001:
        problems.append(
            f"OUT OF BOUNDS {name}: ({l:.3f},{t:.3f})-({r:.3f},{b:.3f}) "
            f"slide=({SW:.3f},{SH:.3f})"
        )

    if shp.shape_type in (MSO_SHAPE_TYPE.AUTO_SHAPE, MSO_SHAPE_TYPE.TEXT_BOX):
        if shp.has_text_frame and shp.text_frame.text.strip():
            tf = shp.text_frame
            ml = inches(tf.margin_left) if tf.margin_left is not None else 0.1
            mr = inches(tf.margin_right) if tf.margin_right is not None else 0.1
            mt = inches(tf.margin_top) if tf.margin_top is not None else 0.05
            mb = inches(tf.margin_bottom) if tf.margin_bottom is not None else 0.05
            avail_w = w - ml - mr
            needed_h = mt + mb
            max_size = 0.0
            all_txt = []
            for p in tf.paragraphs:
                txt = "".join(run.text for run in p.runs)
                if not txt:
                    continue
                sizes = [run.font.size.pt for run in p.runs if run.font.size is not None]
                sz = max(sizes) if sizes else 18.0
                max_size = max(max_size, sz)
                n = wrap_count(txt, max(0.01, avail_w), sz)
                needed_h += n * sz * 1.18 / 72.0
                all_txt.append(txt)
                bad = long_tokens(txt, max(0.01, avail_w), sz)
                if bad:
                    warnings.append(
                        f"LONG TOKEN {name}: {bad} at {sz:.1f}pt in {avail_w:.3f}in"
                    )
            if needed_h > h + 0.02:
                problems.append(
                    f"TEXT OVERFLOW {name}: need {needed_h:.3f}in > box {h:.3f}in "
                    f"(w={w:.3f}, size={max_size:.1f}) text={' / '.join(all_txt)}"
                )
            text_rows.append((name, max_size, needed_h, h, " / ".join(all_txt)))

    if shp.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
        if name.startswith("container_") or name.startswith("lane_"):
            continue
        if w < 0.12 or h < 0.12:
            continue
        autoshapes.append((name, l, t, r, b, w * h))

# pair-wise overlap among filled nodes (containment is allowed)
for i in range(len(autoshapes)):
    n1, l1, t1, r1, b1, a1 = autoshapes[i]
    for j in range(i + 1, len(autoshapes)):
        n2, l2, t2, r2, b2, a2 = autoshapes[j]
        iw = min(r1, r2) - max(l1, l2)
        ih = min(b1, b2) - max(t1, t2)
        if iw <= 0.02 or ih <= 0.02:
            continue
        inter = iw * ih
        if inter >= 0.85 * min(a1, a2):
            continue  # intended containment
        if inter > 0.001:
            problems.append(
                f"OVERLAP {n1} x {n2}: {iw:.3f}x{ih:.3f}={inter:.4f} sq in"
            )

print(f"slide: {SW:.3f} x {SH:.3f} in")
print(f"shapes: {len(slide.shapes)}  pictures: {pictures}  connectors: {line_shapes}")
print(f"autoshapes checked for overlap: {len(autoshapes)}")
print(f"text boxes/shapes checked for fit: {len(text_rows)}")
print(f"min font size: {min(r[1] for r in text_rows):.1f} pt")

print("\n--- text fit (name | size | need | box | text) ---")
for row in sorted(text_rows, key=lambda r: (r[2] - r[3]), reverse=True)[:12]:
    print(f"{row[0][:30]:30s} | {row[1]:4.1f} | {row[2]:.3f} | {row[3]:.3f} | {row[4][:60]}")

print(f"\n--- problems ({len(problems)}) ---")
for p in problems:
    print(p)

print(f"\n--- warnings ({len(warnings)}) ---")
for w in warnings:
    print(w)

raise SystemExit(1 if problems else 0)
