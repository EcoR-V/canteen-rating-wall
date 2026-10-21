# -*- coding: utf-8 -*-
"""为所有缺图菜品生成统一风格的菜品卡（渐变底 + 菜系标签 + 菜名）"""
import os
import re
import sqlite3
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "canteen.db")
OUT = os.path.join(BASE, "uploads")
W, H = 720, 540

# 菜系：(中文名, 起始色, 结束色)
CATS = {
    "noodle":  ("面食", (196, 112, 59), (122, 62, 26)),
    "chicken": ("禽肉", (208, 138, 62), (140, 78, 26)),
    "spicy":   ("麻辣", (192, 57, 43), (110, 26, 18)),
    "rice":    ("米饭", (217, 160, 60), (150, 98, 22)),
    "dumpling":("面点", (201, 160, 106), (128, 96, 52)),
    "soup":    ("汤粥", (184, 134, 91), (116, 76, 44)),
    "meat":    ("荤菜", (169, 75, 50), (100, 38, 22)),
    "dessert": ("甜品", (217, 139, 160), (150, 78, 100)),
    "veg":     ("素菜", (122, 158, 91), (66, 98, 52)),
    "drink":   ("饮品", (201, 138, 91), (128, 78, 44)),
}

RULES = [
    ("dessert", r"甜|奶|果汁|鲜榨|沙拉|蛋糕|烘焙|糖水|鲜花饼|酥|双皮|甘露|果切|果捞|芝麻糊|西瓜汁|橙汁|吐司|面包"),
    ("drink",   r"汁|茶|咖啡|豆浆|酸梅"),
    ("dumpling",r"饺|馄饨|包|小笼|锅贴|烧麦|汤圆|馍"),
    ("noodle",  r"面|粉|米线|拉面|泡馍|肠粉|河粉|乌冬|意面|烩面|刀削|渔粉|拌粉|苕粉|粉丝"),
    ("spicy",   r"麻辣|香锅|冒菜|水煮|螺蛳|火锅|麻婆|辣子|干锅|酸辣粉"),
    ("rice",    r"饭|盖浇|炒饭|石锅|煲仔|咖喱|蛋包|寿司"),
    ("soup",    r"汤|粥|羹|煨|砂锅"),
    ("chicken", r"鸡|鸭|鹅|鸽"),
    ("meat",    r"肉|排骨|红烧|卤|叉烧|烧腊|猪脚|羊|牛|炖|烤|串|鱼|虾|蟹|肘|蹄"),
    ("veg",     r"菜|豆|茄|瓜|菌|蔬|素|藕|笋|菇|豆腐"),
]

FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def category_of(name):
    for key, pat in RULES:
        if re.search(pat, name):
            return key
    return "meat"


def make_card(name, cat):
    cn, c1, c2 = CATS[cat]
    img = Image.new("RGB", (W, H), c1)
    d = ImageDraw.Draw(img)
    # 垂直渐变
    for y in range(H):
        d.line([(0, y), (W, y)], fill=lerp(c1, c2, y / H))

    # 右上柔光
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W * 0.42, -H * 0.55, W * 1.35, H * 0.62], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img = Image.composite(Image.new("RGB", (W, H), (255, 255, 255)), img, glow)

    # 左下暗角
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    sd.ellipse([-W * 0.35, H * 0.45, W * 0.75, H * 1.5], fill=90)
    shade = shade.filter(ImageFilter.GaussianBlur(80))
    img = Image.composite(Image.new("RGB", (W, H), (20, 12, 6)), img, shade)

    d = ImageDraw.Draw(img)

    # 半透明装饰层（圆环 + 标签底）
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([W - 250, H - 250, W + 90, H + 90], outline=(255, 255, 255, 70), width=2)
    od.ellipse([W - 180, H - 180, W + 20, H + 20], outline=(255, 255, 255, 46), width=2)

    tag_font = ImageFont.truetype(FONT_REG, 25)
    tw = od.textlength(cn, font=tag_font)
    od.rounded_rectangle([44, 40, 44 + tw + 34, 40 + 44], radius=22, fill=(255, 255, 255, 46))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)
    d.text((44 + 17, 49), cn, font=tag_font, fill=(255, 255, 255))

    # 菜名：按长度自动换行 + 自适应字号
    text = name
    n = len(text)
    if n <= 4:
        size, per_line = 96, 6
    elif n <= 6:
        size, per_line = 82, 7
    elif n <= 9:
        size, per_line = 64, 8
    elif n <= 13:
        size, per_line = 50, 10
    else:
        size, per_line = 42, 12
    font = ImageFont.truetype(FONT_BOLD, size)
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= per_line:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    lines = lines[:2]

    line_h = int(size * 1.28)
    total_h = line_h * len(lines)
    y0 = H - 70 - total_h
    for i, ln in enumerate(lines):
        w = d.textlength(ln, font=font)
        x = 44
        # 阴影
        d.text((x + 2, y0 + i * line_h + 3), ln, font=font, fill=(0, 0, 0, 90))
        d.text((x, y0 + i * line_h), ln, font=font, fill=(255, 255, 255))

    # 底部细线
    d.line([44, H - 46, 44 + 72, H - 46], fill=(255, 255, 255, 130), width=3)

    return img


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, name, photo FROM dishes").fetchall()
    made = skipped = 0
    for r in rows:
        if r["photo"]:
            skipped += 1
            continue
        cat = category_of(r["name"])
        img = make_card(r["name"], cat)
        fname = "auto-dish-%d.jpg" % r["id"]
        img.save(os.path.join(OUT, fname), "JPEG", quality=84, optimize=True)
        conn.execute("UPDATE dishes SET photo=? WHERE id=?", (fname, r["id"]))
        made += 1
    conn.commit()
    conn.close()
    print("生成 %d 张，保留原图 %d 张，共 %d 个菜品" % (made, skipped, len(rows)))


if __name__ == "__main__":
    main()
