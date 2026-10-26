# -*- coding: utf-8 -*-
"""Generate an editable L1-L4 pipeline diagram as a native-shape PPTX.

Every node, file chip, arrow and label is a real PowerPoint shape (no image),
so the user can edit text, move shapes and change colours directly.

Run:  py -I make_l1_l4_pptx.py
Out:  l1-l4-pipeline.pptx
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
OUT = HERE / "l1-l4-pipeline.pptx"
FONT = "Microsoft YaHei"
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"

# ---------- palette ----------
INK = RGBColor(0x11, 0x18, 0x27)
MUTED = RGBColor(0x6B, 0x72, 0x80)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
SRC = RGBColor(0x6B, 0x72, 0x80)
SRC_BG = RGBColor(0xF3, 0xF4, 0xF6)
L1 = RGBColor(0x25, 0x63, 0xEB)
L1_BG = RGBColor(0xEF, 0xF6, 0xFF)
L2 = RGBColor(0x0D, 0x94, 0x88)
L2_BG = RGBColor(0xF0, 0xFD, 0xFA)
L2_CHIP = RGBColor(0x14, 0xB8, 0xA6)
L3 = RGBColor(0xD9, 0x77, 0x06)
L3_BG = RGBColor(0xFF, 0xFB, 0xEB)
L4 = RGBColor(0xDC, 0x26, 0x26)
L4_BG = RGBColor(0xFE, 0xF2, 0xF2)
GREEN = RGBColor(0x16, 0xA3, 0x4A)
AMBER = RGBColor(0xD9, 0x77, 0x06)
BLUE = RGBColor(0x25, 0x63, 0xEB)
RED = RGBColor(0xDC, 0x26, 0x26)
LINE_GRAY = RGBColor(0x9C, 0xA3, 0xAF)
SOFT_GRAY = RGBColor(0xE5, 0xE7, 0xEB)

# ---------- geometry (inches) ----------
SW, SH = 13.333, 7.5
TITLE_Y, TITLE_H = 0.12, 0.36
SUB_Y, SUB_H = 0.49, 0.22
HEAD_Y = 0.76
TOP, BOT = 0.92, 7.12
MID_H = BOT - TOP

SRC_X, SRC_W = 0.25, 1.47
L1_X, L1_W = 1.85, 2.70
L2_X, L2_W = 4.68, 4.44
L3_X, L3_W = 9.25, 2.05
L4_X, L4_W = 11.43, 1.67

LANE_TOP, LANE_H, LANE_GAP = 1.45, 0.76, 0.055
LANE_CENTERS = [LANE_TOP + i * (LANE_H + LANE_GAP) + LANE_H / 2 for i in range(7)]

S_LABELS = [
    ("S1 工具", "tool"),
    ("S2 仓", "repo"),
    ("S3 部件", "component"),
    ("S4 工具链", "toolchain"),
    ("S5 库/符号", "lib + symbol"),
    ("S6 头文件", "header gap"),
    ("S7 配置", "config + arch"),
]
EXTRACTORS = ["s1_tool.py", "s2_repo.py", "s3_part.py", "s4_toolchain.py",
              "s5_lib.py", "s6_include.py", "s7_config.py"]
CHIP_LINES = [
    ["clang", "python3", "make", "hb", "bash"],
    ["474\nprojects", "mount\npaths", "includes", "linkfile", "groups"],
    ["hilog", "hilog_native", "qemu_arm\nlinux_chipset", "system_arm64\ndefault", "deps"],
    ["target_cpu=\narm64", "board_cpu=\ncortex-a7", "aarch64-\nlinux-ohos", "clang /\nllvm-ar"],
    ["-lhilog", "libstdc++", "ENTRY\n(_start)", "libhilog.map", "force_link\nlibs"],
    ["uint32_t ×\ncstdint", "pthread_t", "#include gap"],
    ["qemu-arm-linux\nstandard_defconfig", "type/\nstandard", "CONFIG_*", "board_arch=\narmv7-a"],
]
SOURCE_ROWS = [
    ("manifest/", ["default.xml"]),
    ("build/", ["BUILD.gn · Makefile"]),
    ("productdefine/", ["products/*.json"]),
    ("vendor/", ["config.json"]),
    ("device/", ["config.gni", "ohos.build"]),
    ("kernel/linux/", ["*_defconfig"]),
    ("base/", ["bundle.json · *.cpp", "*.ld · *.map"]),
]
VALIDATORS = [
    ("V-tree", "树模型"),
    ("V-exists", "存在性"),
    ("V-identity", "身份一致"),
    ("V-consistency", "声明一致"),
    ("V-reach", "可达性"),
]

# ---------- primitives ----------
def rect(slide, x, y, w, h, fill, line=None, line_w=0.75, radius=0.08, name=None):
    shp = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
    )
    try:
        shp.adjustments[0] = radius
    except Exception:
        pass
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(line_w)
    shp.shadow.inherit = False
    if name:
        shp.name = name
    return shp


def set_ea_font(run, font=FONT):
    rpr = run._r.get_or_add_rPr()
    ea = rpr.find(qn("a:ea"))
    if ea is None:
        ea = rpr.makeelement(qn("a:ea"), {})
        rpr.append(ea)
    ea.set("typeface", font)


def text(slide, x, y, w, h, lines, size=9, color=INK, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE,
         margins=(0.02, 0.02, 0.01, 0.01), line_spacing=0.92, name=None):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = Inches(margins[0])
    tf.margin_right = Inches(margins[1])
    tf.margin_top = Inches(margins[2])
    tf.margin_bottom = Inches(margins[3])
    tf.vertical_anchor = anchor
    if isinstance(lines, str):
        lines = [lines]
    for i, item in enumerate(lines):
        if isinstance(item, tuple):
            txt, opts = item
        else:
            txt, opts = item, {}
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = opts.get("align", align)
        p.line_spacing = opts.get("line_spacing", line_spacing)
        p.space_before = Pt(0)
        p.space_after = Pt(0)
        run = p.add_run()
        run.text = txt
        run.font.size = Pt(opts.get("size", size))
        run.font.bold = opts.get("bold", bold)
        run.font.color.rgb = opts.get("color", color)
        run.font.name = FONT
        set_ea_font(run)
    if name:
        box.name = name
    return box


def shape_text(shp, lines, size=8, color=INK, bold=False, align=PP_ALIGN.CENTER,
               line_spacing=0.88, margins=(0.01, 0.01, 0.01, 0.01),
               anchor=MSO_ANCHOR.MIDDLE):
    tf = shp.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = Inches(margins[0])
    tf.margin_right = Inches(margins[1])
    tf.margin_top = Inches(margins[2])
    tf.margin_bottom = Inches(margins[3])
    tf.vertical_anchor = anchor
    if isinstance(lines, str):
        lines = [lines]
    for i, item in enumerate(lines):
        if isinstance(item, tuple):
            txt, opts = item
        else:
            txt, opts = item, {}
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = opts.get("align", align)
        p.line_spacing = opts.get("line_spacing", line_spacing)
        p.space_before = Pt(0)
        p.space_after = Pt(0)
        run = p.add_run()
        run.text = txt
        run.font.size = Pt(opts.get("size", size))
        run.font.bold = opts.get("bold", bold)
        run.font.color.rgb = opts.get("color", color)
        run.font.name = FONT
        set_ea_font(run)


def _tail_arrow(line_fmt):
    ln = line_fmt._get_or_add_ln()
    for tag in ("a:headEnd", "a:tailEnd"):
        for el in ln.findall(qn(tag)):
            ln.remove(el)
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "sm", "len": "sm"}))


def line(slide, x1, y1, x2, y2, color=LINE_GRAY, width=0.75, dash=None,
         arrow=False, name=None):
    conn = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2)
    )
    conn.line.color.rgb = color
    conn.line.width = Pt(width)
    if dash is not None:
        conn.line.dash_style = dash
    if arrow:
        _tail_arrow(conn.line)
    if name:
        conn.name = name
    return conn


def chip(slide, x, y, w, h, txt, size=7.0):
    shp = rect(slide, x, y, w, h, WHITE, L2_CHIP, 0.6, radius=0.18, name="chip")
    shape_text(shp, txt.split("\n"), size=size, color=RGBColor(0x13, 0x4E, 0x4A),
               line_spacing=0.86, margins=(0.008, 0.008, 0.006, 0.006))
    return shp


# ---------- document ----------
prs = Presentation()
prs.slide_width = Inches(SW)
prs.slide_height = Inches(SH)
slide = prs.slides.add_slide(prs.slide_layouts[6])
slide.background.fill.solid()
slide.background.fill.fore_color.rgb = WHITE

# title / subtitle
text(slide, 0.28, TITLE_Y, 12.8, TITLE_H,
     "编译前预检流水线：L1 扫描 → L2 提取 → L3 验证 → L4 修复",
     size=19, bold=True, color=INK)
text(slide, 0.30, SUB_Y, 12.8, SUB_H,
     "输入是整棵源码树；L1 只读路径/文件名/首行，L2 才读内容；L3 产出 findings，L4 修复后回环",
     size=8.5, color=MUTED)

# section headers
for x, w, label in [
    (SRC_X, 1.42, "输入 · 源码树"),
    (L1_X, 2.10, "L1 · 扫描与分类"),
    (L2_X, 3.60, "L2 · 提取"),
    (L3_X, 1.70, "L3 · 验证"),
    (L4_X, 1.70, "L4 · 修复"),
]:
    text(slide, x, HEAD_Y, w, 0.18, label, size=9, bold=True, color=MUTED)

# containers
rect(slide, SRC_X, TOP, SRC_W, MID_H, SRC_BG, SRC, 0.8, radius=0.035, name="container_source")
rect(slide, L1_X, TOP, L1_W, MID_H, L1_BG, L1, 0.8, radius=0.035, name="container_L1")
rect(slide, L2_X, TOP, L2_W, MID_H, L2_BG, L2, 0.8, radius=0.035, name="container_L2")
rect(slide, L3_X, TOP, L3_W, MID_H, L3_BG, L3, 0.8, radius=0.035, name="container_L3")
rect(slide, L4_X, TOP, L4_W, MID_H, L4_BG, L4, 0.8, radius=0.035, name="container_L4")

# ---------- source tree ----------
text(slide, SRC_X + 0.08, TOP + 0.08, SRC_W - 0.16, 0.18,
     "源码树 /", size=8.5, bold=True, color=INK)
row_top, row_h, row_gap = 1.52, 0.56, 0.06
for i, (branch, files) in enumerate(SOURCE_ROWS):
    y = row_top + i * (row_h + row_gap)
    row = rect(slide, SRC_X + 0.08, y, SRC_W - 0.16, row_h, WHITE, SOFT_GRAY, 0.5,
               radius=0.08, name=f"source_row_{i}")
    lines = [(branch, {"size": 7.8, "bold": True, "align": PP_ALIGN.LEFT})]
    for f in files:
        lines.append((f, {"size": 6.2, "color": MUTED, "align": PP_ALIGN.LEFT}))
    shape_text(row, lines, align=PP_ALIGN.LEFT, margins=(0.05, 0.04, 0.03, 0.02),
               anchor=MSO_ANCHOR.TOP)

# arrows from tree branches to the L1 entrance rail
RAIL_X = L1_X + 0.05
for i in range(len(SOURCE_ROWS)):
    y = row_top + i * (row_h + row_gap) + row_h / 2
    line(slide, SRC_X + SRC_W - 0.02, y, RAIL_X, y, LINE_GRAY, 0.7, arrow=True,
         name=f"src_to_L1_{i}")
# entrance rail inside L1
line(slide, RAIL_X, 1.20, RAIL_X, 5.92, LINE_GRAY, 1.0, name="L1_entrance_rail")

# ---------- L1: horizontal chain, centered, fan out from the middle ----------
CHAIN_H = 0.44
CHAIN_TOP = TOP + MID_H / 2 - CHAIN_H / 2   # vertical middle of L1
CHAIN_CY = CHAIN_TOP + CHAIN_H / 2
CHAIN_GAP = 0.05
CHAIN_WIDTHS = [0.31, 0.46, 0.31]
CHAIN_TOTAL = sum(CHAIN_WIDTHS) + CHAIN_GAP * (len(CHAIN_WIDTHS) - 1)
CHAIN_X = L1_X + (L1_W - CHAIN_TOTAL) / 2   # horizontally centered in L1

# source rail -> the first chain node
line(slide, RAIL_X, CHAIN_CY, CHAIN_X, CHAIN_CY, L1, 1.2, arrow=True,
     name="rail_to_walk")

x = CHAIN_X
for i, (title, sub, nm, w) in enumerate([
    ("遍历", "walk", "L1_walk", CHAIN_WIDTHS[0]),
    ("识别", "后缀/名/路径", "L1_identify", CHAIN_WIDTHS[1]),
    ("分类", "7 类", "L1_classify", CHAIN_WIDTHS[2]),
]):
    shp = rect(slide, x, CHAIN_TOP, w, CHAIN_H, WHITE, L1, 0.8,
               radius=0.12, name=nm)
    shape_text(shp, [(title, {"size": 9, "bold": True}),
                     (sub, {"size": 5.3, "color": MUTED})])
    if i < 2:
        line(slide, x + w, CHAIN_CY, x + w + CHAIN_GAP, CHAIN_CY,
             L1, 1.0, arrow=True, name=f"chain_step_{i+1}")
    x += w + CHAIN_GAP
CLASS_RIGHT = x - CHAIN_GAP

# classify -> vertical bus -> 7 S nodes
BUS_X = 3.85
line(slide, CLASS_RIGHT, CHAIN_CY, BUS_X, CHAIN_CY, L1, 0.9,
     arrow=True, name="classify_to_bus")
line(slide, BUS_X, LANE_CENTERS[0], BUS_X, LANE_CENTERS[-1], L1, 0.8,
     name="classify_bus")

S_X, S_W, S_H = 3.91, 0.58, 0.46
for i, (label, sub) in enumerate(S_LABELS):
    cy = LANE_CENTERS[i]
    line(slide, BUS_X, cy, S_X, cy, L1, 0.7, arrow=True, name=f"bus_to_S{i+1}")
    shp = rect(slide, S_X, cy - S_H / 2, S_W, S_H, WHITE, L1, 0.8, radius=0.12,
               name=f"S{i+1}")
    shape_text(shp, [(label, {"size": 7.8, "bold": True}),
                     (sub, {"size": 5.2, "color": MUTED})])

# ---------- L2 lanes ----------
for i in range(7):
    y = LANE_TOP + i * (LANE_H + LANE_GAP)
    rect(slide, L2_X + 0.05, y, L2_W - 0.10, LANE_H, WHITE, None,
         radius=0.04, name=f"lane_{i}")

EXT_X, EXT_W, EXT_H = L2_X + 0.08, 0.76, 0.48
CHIP_X0, CHIP_X1 = L2_X + 0.98, L2_X + L2_W - 0.10
CHIP_H = 0.36
for i, (filename, items) in enumerate(zip(EXTRACTORS, CHIP_LINES)):
    cy = LANE_CENTERS[i]
    # arrow S -> extractor
    line(slide, S_X + S_W, cy, EXT_X, cy, L1, 0.9, arrow=True, name=f"S{i+1}_to_extractor")
    # extractor
    shp = rect(slide, EXT_X, cy - EXT_H / 2, EXT_W, EXT_H, RGBColor(0xE6, 0xFF, 0xFA),
               L2, 0.8, radius=0.12, name=f"extractor_{i+1}")
    shape_text(shp, filename, size=6.2, bold=True, color=RGBColor(0x11, 0x5E, 0x59))
    # chips
    n = len(items)
    gap = 0.05 if n >= 5 else 0.06
    cw = (CHIP_X1 - CHIP_X0 - (n - 1) * gap) / n
    if i in (2, 6):
        size = 6.2
    else:
        size = 6.4 if n >= 5 else (7.0 if n == 4 else 7.4)
    for j, item in enumerate(items):
        x = CHIP_X0 + j * (cw + gap)
        chip(slide, x, cy - CHIP_H / 2, cw, CHIP_H, item, size=size)
    # lane fan-in bracket
    bracket_x = CHIP_X1 + 0.03
    line(slide, CHIP_X1, cy - CHIP_H / 2, bracket_x, cy - CHIP_H / 2, L2_CHIP, 0.5)
    line(slide, CHIP_X1, cy + CHIP_H / 2, bracket_x, cy + CHIP_H / 2, L2_CHIP, 0.5)
    line(slide, bracket_x, cy - CHIP_H / 2, bracket_x, cy + CHIP_H / 2, L2_CHIP, 0.7)
    line(slide, bracket_x, cy, L3_X - 0.05, cy, L2_CHIP, 0.7, name=f"lane{i+1}_to_bus")

# need bus at L3 left gap
BUS3_X = L3_X - 0.05
line(slide, BUS3_X, 1.50, BUS3_X, 6.80, L2_CHIP, 0.9, name="demand_bus")
text(slide, L3_X + 0.05, 1.26, 0.90, 0.16, "需求集合", size=5.8, color=MUTED,
     align=PP_ALIGN.LEFT)

# ---------- L3 validators ----------
V_X, V_W, V_H = L3_X + 0.13, L3_W - 0.37, 0.72
V_FIRST_C, V_LAST_C = 1.91, 6.21
V_STEP = (V_LAST_C - V_FIRST_C) / (len(VALIDATORS) - 1)
V_BUS_X = V_X + V_W + 0.07
for i, (name, hint) in enumerate(VALIDATORS):
    cy = V_FIRST_C + i * V_STEP
    line(slide, BUS3_X, cy, V_X, cy, L3, 0.8, arrow=True, name=f"bus_to_V{i+1}")
    shp = rect(slide, V_X, cy - V_H / 2, V_W, V_H, WHITE, L3, 0.8, radius=0.10,
               name=f"validator_{i+1}")
    shape_text(shp, [(name, {"size": 8.0, "bold": True}),
                     (hint, {"size": 6.0, "color": MUTED})])
    line(slide, V_X + V_W, cy, V_BUS_X, cy, L3, 0.5, name=f"V{i+1}_to_findings_bus")

# findings bus (the arrow into triage is drawn after the triage box)
line(slide, V_BUS_X, 1.37, V_BUS_X, 6.21, L3, 1.0, name="findings_bus")

# ---------- L4 ----------
L4_LEFT, L4_RIGHT = L4_X + 0.08, L4_X + L4_W - 0.08
TRI_Y, TRI_H = 1.15, 0.44
shp = rect(slide, L4_LEFT, TRI_Y, L4_RIGHT - L4_LEFT, TRI_H,
           RGBColor(0xFE, 0xE2, 0xE2), RED, 0.9, radius=0.12, name="triage")
shape_text(shp, [("triage 分诊", {"size": 8, "bold": True}),
                 ("findings[]", {"size": 5.6, "color": RGBColor(0x99, 0x1B, 0x1B)}),
                 ("block · warn · info", {"size": 5.0, "color": RGBColor(0x99, 0x1B, 0x1B)})],
           color=RGBColor(0x99, 0x1B, 0x1B))
line(slide, V_BUS_X, 1.37, L4_LEFT, 1.37, RED, 2.0, arrow=True, name="L3_to_L4")

ACTIONS = [
    ("apply_recipe", GREEN),
    ("propose_patch", GREEN),
    ("escalate", AMBER),
    ("abandon", MUTED),
]
ACT_TOP, ACT_H, ACT_GAP = 1.78, 0.30, 0.075
for i, (name, color) in enumerate(ACTIONS):
    y = ACT_TOP + i * (ACT_H + ACT_GAP)
    fill = RGBColor(0xF0, 0xFD, 0xF4) if color == GREEN else (
        RGBColor(0xFF, 0xFB, 0xEB) if color == AMBER else RGBColor(0xF3, 0xF4, 0xF6))
    shp = rect(slide, L4_LEFT, y, L4_RIGHT - L4_LEFT, ACT_H, fill, color, 0.7,
               radius=0.14, name=f"action_{name}")
    shape_text(shp, name, size=6.7, color=color, bold=False)

# status list
STAT_Y = 3.55
rect(slide, L4_LEFT, STAT_Y, L4_RIGHT - L4_LEFT, 1.55, WHITE, RGBColor(0xFC, 0xA5, 0xA5),
     0.6, radius=0.06, name="status_box")
text(slide, L4_LEFT + 0.05, STAT_Y + 0.06, L4_RIGHT - L4_LEFT - 0.10, 0.18,
     "fix_status", size=6.0, bold=True, color=RED)
text(slide, L4_LEFT + 0.05, STAT_Y + 0.26, L4_RIGHT - L4_LEFT - 0.10, 1.18,
     ["pending", "fixed", "patched", "escalated", "rolled_back", "abandoned"],
     size=6.0, color=MUTED, line_spacing=0.95)

# loop mark
text(slide, L4_LEFT + 0.04, 5.36, 0.68, 0.20, "↻ 修复后重跑", size=6.6, color=RED,
     bold=True)

# ---------- L4 feedback loop ----------
LOOP_Y = 7.34
LOOP_X = L4_RIGHT - 0.72
L1_LOOP_X = CHAIN_X + CHAIN_TOTAL / 2
line(slide, LOOP_X, 5.10, LOOP_X, LOOP_Y, RED, 1.2, dash=MSO_LINE_DASH_STYLE.DASH)
line(slide, LOOP_X, LOOP_Y, L1_LOOP_X, LOOP_Y, RED, 1.2,
     dash=MSO_LINE_DASH_STYLE.DASH)
# branches up into L2, L3, L1
for bx, target in [(L2_X + 0.42, "L2"), (L3_X + 0.08, "L3"), (L1_LOOP_X, "L1")]:
    line(slide, bx, LOOP_Y, bx, BOT, RED, 1.2,
         dash=MSO_LINE_DASH_STYLE.DASH, arrow=True, name=f"loop_to_{target}")
text(slide, L2_X + 0.55, 7.16, 4.0, 0.15,
     "修复后重跑：L2 重提取 → L3 重验证；必要时回 L1", size=6.3, color=RED)
text(slide, 0.28, 7.28, 3.0, 0.14,
     "依据：docs/08-对象与文件特征调研.md（S1–S7）", size=5.5, color=MUTED)

# ---------- legend ----------
LEG_X, LEG_Y, LEG_W, LEG_H = SRC_X + 0.08, 6.20, SRC_W - 0.16, 0.82
rect(slide, LEG_X, LEG_Y, LEG_W, LEG_H, WHITE, SOFT_GRAY, 0.5, radius=0.06,
     name="legend_box")
text(slide, LEG_X + 0.05, LEG_Y + 0.03, LEG_W - 0.10, 0.14, "图例", size=6.0,
     bold=True, color=INK)
# samples
line(slide, LEG_X + 0.05, LEG_Y + 0.24, LEG_X + 0.32, LEG_Y + 0.24, BLUE, 1.8)
text(slide, LEG_X + 0.36, LEG_Y + 0.18, LEG_W - 0.38, 0.12, "主链路", size=5.4,
     color=MUTED)
line(slide, LEG_X + 0.05, LEG_Y + 0.40, LEG_X + 0.32, LEG_Y + 0.40, LINE_GRAY, 0.6)
text(slide, LEG_X + 0.36, LEG_Y + 0.34, LEG_W - 0.38, 0.12, "文件/提取分叉", size=5.4,
     color=MUTED)
line(slide, LEG_X + 0.05, LEG_Y + 0.56, LEG_X + 0.32, LEG_Y + 0.56, RED, 1.1,
     dash=MSO_LINE_DASH_STYLE.DASH)
text(slide, LEG_X + 0.36, LEG_Y + 0.50, LEG_W - 0.38, 0.12, "修复回环", size=5.4,
     color=MUTED)
for j, (label, color) in enumerate([("block", RED), ("warn", AMBER), ("info", BLUE)]):
    x = LEG_X + 0.06 + j * 0.40
    rect(slide, x, LEG_Y + 0.70, 0.07, 0.07, color, None, radius=0.02)
    text(slide, x + 0.10, LEG_Y + 0.655, 0.30, 0.13, label, size=5.4, color=MUTED)

prs.save(OUT)
print(f"saved: {OUT}")
