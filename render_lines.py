# -*- coding: utf-8 -*-
"""
逐行原位替换渲染器：用于「多行 UI 排版」图（如 X/Twitter 风格 SNS 帖）。

与 render_fit_source 的区别：
  render_fit_source  把整段文字居中画进一个对话框 —— 适合对话气泡
  本渲染器           按 OCR 找到的**每一行**，在原坐标处替换为对应译文
                     —— 适合昵称/handle/正文分处不同位置的 UI 排版

做法：
  1. 以原图为底，按行包围盒抹掉原文（填底色）
  2. 每行译文单独排版，字号取「与原文行高相当」，
     再按 (原行宽 / 译行宽) 做水平缩放，保证不越界、不压到旁边的元素
  3. 输出与源图同尺寸、同颜色模式
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"]
GAP = 2


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONTS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    raise RuntimeError("no CJK font")


def parse_boxes(path: Path) -> list[dict]:
    """解析 ocr-boxes.ps1 的产物 -> [{idx, x1,y1,x2,y2, text}, ...]

    行格式: L1<TAB>y=18..31 x=126..245 h=13<TAB>@shino_k 05300350
    """
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.startswith("L") or "\ty=" not in raw:
            continue
        parts = raw.split("\t", 2)
        if len(parts) != 3:
            continue
        idx = parts[0].strip()
        geo = parts[1]                     # y=18..31 x=126..245 h=13
        text = parts[2].strip()
        try:
            yseg, xseg = geo.split(" x=")
            y1, y2 = (int(v) for v in yseg.replace("y=", "").split(".."))
            xseg = xseg.split(" h=")[0]
            x1, x2 = (int(v) for v in xseg.split(".."))
            out.append({"idx": idx, "y1": y1, "y2": y2, "x1": x1, "x2": x2, "text": text})
        except Exception as e:
            print("  [解析跳过] %r : %s" % (raw[:50], e))
    return out


def detect_bg(img: Image.Image):
    from collections import Counter
    px = img.load()
    c = Counter()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a > 128:
                c[(r, g, b)] += 1
    return c.most_common(1)[0][0] if c else (255, 255, 255)


def region_is_complex(px, x1, y1, x2, y2, bg, max_colors: int = 24) -> bool:
    """判断矩形区域是否像「头像/插画」（颜色种类极多）。

    注意：这个判据**只能用于「框内是否混入头像」的粗略保护**，
    不能用来判断"这行是不是头像" —— 白底上的抗锯齿文字本身就有几十种颜色，
    实测会把普通对话行全部误判为插画（一旦误判就会跳过抹除，整张图不替换）。
    因此默认 **不启用**（protect_complex=False），改用几何判断：
    对话框素材里文字盒 x≥48、头像在 x≤47，本就不重叠。
    """
    seen = set()
    for y in range(max(0, y1), y2 + 1):
        for x in range(max(0, x1), x2 + 1):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 60:
                seen.add((r // 16, g // 16, b // 16))   # 粗量化：忽略文字抗锯齿
                if len(seen) > max_colors:
                    return True
    return False


def detect_bg_and_fg(img: Image.Image, box=None):
    """返回 (底色, 文字色)。

    - 底色 = 不透明区里最常见的颜色（气泡底，白/蓝/灰都可能）
    - 文字色 = 「与底色不同」的像素里最常见的颜色（白底->深色字、蓝底->白字）
    只统计 box 区域（若给了）会更准，避免把交互图标算成文字色。
    """
    from collections import Counter
    px = img.load()
    x1, y1, x2, y2 = box if box else (0, 0, img.width - 1, img.height - 1)
    bgc, fgc = Counter(), Counter()
    for y in range(max(0, y1), min(img.height, y2 + 1)):
        for x in range(max(0, x1), min(img.width, x2 + 1)):
            r, g, b, a = px[x, y]
            if a > 128:
                bgc[(r, g, b)] += 1
    if not bgc:
        return (255, 255, 255), (0, 0, 0)
    bg = bgc.most_common(1)[0][0]
    for y in range(max(0, y1), min(img.height, y2 + 1)):
        for x in range(max(0, x1), min(img.width, x2 + 1)):
            r, g, b, a = px[x, y]
            if a > 128 and abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 60:
                fgc[(r, g, b)] += 1
    fg = fgc.most_common(1)[0][0] if fgc else (0, 0, 0)
    return bg, fg


def _rgb_of(v, fallback):
    if v is None:
        return fallback
    if isinstance(v, str):
        t = v.lstrip("#")
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(v)


def render_lines(translations: dict[str, str], src_png: Path, boxes_file: Path,
                 out_png: Path, bg_color=None, text_color=None,
                 protect_complex: bool = False,
                 local_color: bool = True) -> tuple[int, int]:
    """translations: {行号如 'L2': 译文}。返回 (行数, 实际替换行数)。

    local_color=True（默认）时**逐行局部取色**，这是处理「一张图里有多种底色」的
    唯一可靠做法（实测 partner\\kankei_ayano.png：蓝、粉两种药丸交替，
    全局取色会把蓝色药丸当成"文字色"，抹除时把整条药丸涂成粉色）。
    """
    base = Image.open(src_png).convert("RGBA")
    W, H = base.size
    boxes = parse_boxes(boxes_file)
    targets = [b for b in boxes if translations.get(b["idx"])]
    for b in targets:
        b["_zh"] = translations[b["idx"]]

    # 全局兜底（local_color=False 或逐行取色失败时用）
    probe = (targets[0]["x1"], targets[0]["y1"], targets[0]["x2"], targets[0]["y2"]) if targets else None
    auto_bg, auto_fg = detect_bg_and_fg(base, probe)
    g_bg = _rgb_of(bg_color, auto_bg)
    g_fg = _rgb_of(text_color, auto_fg)

    def line_colors(b):
        """求某一行的 (该行底色, 该行文字色)。

        底色 = 该行盒内最常见的颜色（即药丸/气泡本身的颜色）。
        文字色 = 若底色偏亮则取最深的显著色，偏暗则取最亮的显著色
                 —— 也就是「在底色上能看清的颜色」，天然得到 白底->黑字、蓝/粉底->白字。
        """
        if not local_color:
            return g_bg, g_fg
        px0 = base.load()
        x1, y1 = max(0, b["x1"]), max(0, b["y1"])
        x2, y2 = min(W - 1, b["x2"]), min(H - 1, b["y2"])

        # ── 底色 / 文字色 ────────────────────────────────────────────
        # 判据：**成片实心块**是底色，**细碎笔画**是文字。
        # 实测三种情况的分布，靠亮度或众数都无法统一判断：
        #   SNS 白底黑字   盒内黑 81%（OCR 盒是穿过文字的横条，背景只占极少）
        #   药丸蓝底白字   盒内蓝 61%、白 14%（药丸底色成片）
        #   心情浅底深字   盒内深灰 (97,94,99)，与白底亮度差极小
        # 但"最长横向连续段"能把它们分开：底色是连成一大片的，文字是断开的笔画。
        #   药丸蓝: 最右 6 列整列都是蓝 -> run ≈ 盒宽
        #   SNS 黑 : 汉字笔画宽 2~3px -> run ≤ 3
        cnt, exact, rowruns = {}, {}, {}
        for y in range(y1, y2 + 1):
            run_key, run_len = None, 0
            for x in range(x1, x2 + 1):
                r, g, bl, a = px0[x, y]
                if a <= 128:
                    if run_key is not None:
                        rowruns[run_key] = max(rowruns.get(run_key, 0), run_len)
                    run_key, run_len = None, 0
                    continue
                key = (r // 8 * 8, g // 8 * 8, bl // 8 * 8)
                cnt[key] = cnt.get(key, 0) + 1
                if a == 255:
                    ec = exact.setdefault(key, {})
                    ec[(r, g, bl)] = ec.get((r, g, bl), 0) + 1
                if key == run_key:
                    run_len += 1
                else:
                    if run_key is not None:
                        rowruns[run_key] = max(rowruns.get(run_key, 0), run_len)
                    run_key, run_len = key, 1
            if run_key is not None:
                rowruns[run_key] = max(rowruns.get(run_key, 0), run_len)

        if not cnt:
            return g_bg, g_fg
        box_w = x2 - x1 + 1
        # 候选底色：最长连续段达到盒宽 25% 以上的颜色里，像素数最多的那个
        solid_limit = max(6, int(box_w * 0.25))
        solid = [(k, n) for k, n in cnt.items() if rowruns.get(k, 0) >= solid_limit]
        if solid:
            bg_key = max(solid, key=lambda kv: kv[1])[0]
            ec = exact.get(bg_key) or {}
            bg = max(ec.items(), key=lambda kv: kv[1])[0] if ec else bg_key
        else:
            # 行盒内没有成片颜色（全是文字笔画）—— 说明底不是图内的颜色。
            # 实测 shino_sns\shino_1：整张图 84.7% 是透明，不透明像素里黑字占 42.7%、
            # 灰 30%、近白仅 6.3% —— **图内根本没有"纸面"**，纸面是游戏里的卡片底。
            # 再往盒外取样也没用（相邻行紧挨着，取到的还是文字，实测全是 (0,0,0)）。
            # 因此直接假定浅色底（白），文字取盒内**最深**的显著色。
            bg = (255, 255, 255)
            cands = [(c, n) for c, n in ec.items()
                     if max(abs(c[i] - 255) for i in range(3)) > 60]
            if cands:
                fg_dark = min(cands, key=lambda kv: 0.299 * kv[0][0] + 0.587 * kv[0][1] + 0.114 * kv[0][2])[0]
            else:
                fg_dark = (0, 0, 0)
            return bg, (g_fg if text_color else fg_dark)

        # 文字色 = 离底色最远的那个显著色（按方向分组后取最远，再取像素最多的一组）
        fg = g_fg
        rays = {}
        for c, n in ec.items():
            d = max(abs(c[i] - bg[i]) for i in range(3))
            if d < 40:
                continue
            mx = max(abs(c[i] - bg[i]) for i in range(3))
            direction = tuple(round((c[i] - bg[i]) / mx) for i in range(3))
            cur = rays.get(direction)
            if cur is None or d > cur[0]:
                rays[direction] = (d, c, n)
        if rays:
            fg = max(rays.values(), key=lambda t: t[2])[1]
        if text_color:
            fg = g_fg          # 显式指定时强制覆盖
        return bg, fg

        # 文字色：不能按「亮底取暗字 / 暗底取亮字」判断 —— 实测 partner 的彩色药丸
        # 底色亮度 174（中等偏亮），但上面的文字是**白色**，按亮度方向会取错。
        # 正确判据是「离群色」：文字像素在数量上永远是少数，在色彩上离底色最远。
        # 做法：按「离底色方向」把颜色归一化分组，每组只保留**最远**的那个，
        # 再从各组的代表里挑像素数最多的。
        # 文字色：用白色分层的中位数（在上面算 bg 时已一并求出），
        # 比"离群色"更稳：白底黑字取到黑、蓝底白字取到白。
        fg = auto_fg
        if text_color:
            fg = g_fg          # 显式指定时强制覆盖
        return bg, fg

    for b in targets:
        lb, lf = line_colors(b)
        b["_bg"], b["_fg"] = lb, lf

    d = ImageDraw.Draw(base)
    px = base.load()
    n = 0
    erased = bytearray(W * H)      # 记录「被抹除器处理过」的像素位置

    def occupied(x1, y1, x2, y2, bg_ref) -> bool:
        """该矩形内是否已有**外来**内容（头像/图标等）。

        关键：忽略 erased 标记过的像素 —— 那些是本行原文被抹除后留下的淡淡抗锯齿边缘
        （实测 partner\\kankei_ayano.png 残留 (183,218,254)，与底色最大分量差 63，
        刚好超过抹除阈值 60 而未被清除）。若把它们算作"有内容"，碰撞避让会把字号
        从 16px 一路压到 9px，文字几乎看不见。
        """
        for yy in range(max(0, y1), min(H, y2 + 1)):
            base_i = yy * W
            for xx in range(max(0, x1), min(W, x2 + 1)):
                if erased[base_i + xx]:
                    continue
                r, g, b, a = px[xx, yy]
                if a == 0:
                    continue
                if abs(r - bg_ref[0]) + abs(g - bg_ref[1]) + abs(b - bg_ref[2]) > 30:
                    return True
        return False

    # ── 第一遍：把所有行的原文都抹掉 ──────────────────────────────
    # 必须与绘制分成两遍。若按「每行先抹后画」循环，后处理的行在抹除时会
    # 擦掉先处理的行**已经画好的新文字**（相邻行包围盒纵向重叠时必现；
    # 实测 shino_6 的 L8/L9/L10 相互重叠 -> 回复区出现残缺叠影）。
    # 抹除只动「与底色不同」的像素：既不误伤图标等非底色元素，
    # 也不会破坏别行已抹好的区域。保留 alpha，避免透明区变不透明。
    # 抹除内边距：上下要比 OCR 行盒多留一些 —— OCR 盒常只覆盖墨迹主体，
    # 而药丸/气泡的描边就在附近（实测 partner 的药丸边框在 OCR 盒外 3~4px），
    # 抹除范围太小会让边框落在 erased 标记之外，碰撞避让误判"有内容"而压小字号。
    pad_x, pad_y = 3, 4
    for b in targets:
        ex1, ey1 = max(0, b["x1"] - pad_x), max(0, b["y1"] - pad_y)
        ex2, ey2 = min(W - 1, b["x2"] + pad_x), min(H - 1, b["y2"] + pad_y)
        lb, lf = b["_bg"], b["_fg"]        # 该行自己的底色/文字色
        # 水平吸附：OCR 行包围盒可能漏掉**装饰性笔画**（如破折号「——」，
        # 实测 rino_41 原图「末を——見逃すな」，破折号横跨两段之间但不在任何词框内）。
        # 做法：从行框向左右延伸，只要非该行底色的像素横向间隙 ≤ GAP_TOL 就继续吸附，
        # 遇到较大空白即停 —— 这样既能吃掉破折号，又不会碰到分隔线/交互图标。
        GAP_TOL = 3
        span = [ex1, ex2]
        for direction in (-1, 1):
            x = span[0] if direction < 0 else span[1]
            gap = 0
            while 0 <= x + direction < W and gap <= GAP_TOL:
                x += direction
                found = False
                for y in range(ey1, ey2 + 1):
                    r, g, bb, al = px[x, y]
                    if al and abs(r - lb[0]) + abs(g - lb[1]) + abs(bb - lb[2]) > 60:
                        found = True
                        break
                if found:
                    gap = 0
                    span[0 if direction < 0 else 1] = x
                else:
                    gap += 1
        ex1, ex2 = span
        # 头像保护：若该行盒内混有头像/插画（颜色种类极多），跳过抹除以免误伤
        if protect_complex and region_is_complex(px, ex1, ey1, ex2, ey2, lb):
            print("      [保护] 行 %s 区域像头像/插画，跳过抹除" % b["idx"])
            b["_no_erase"] = True
            continue
        for y in range(ey1, ey2 + 1):
            row_i = y * W
            for x in range(ex1, ex2 + 1):
                r, g, bb, al = px[x, y]
                if al == 0:
                    continue
                erased[row_i + x] = 1
                if abs(r - lb[0]) + abs(g - lb[1]) + abs(bb - lb[2]) > 60:
                    px[x, y] = lb + (al,)

    # ── 第二遍：统一绘制所有译文 ──────────────────────────────────
    # 同一张图内用**统一字号**，并按**行顶锚定**而不是"盒内居中"。
    # 原因：Windows OCR 会把日文逗号「、」的行高抬高（CJK 标点居中于行中），
    # 相邻行的包围盒会互相咬合（实测 shino_6 的 L8/L9/L10：159-176 / 174-192 / 189-204）。
    # 若逐行按各自盒高取字号并垂直居中，这几行必然撞在一起。
    # 统一字号取「各行高的中位数 × 0.8」，行顶锚定让每行各就各位。
    # 把「同一视觉行」的多个片段合并：Windows OCR 常把一句话按列切成多段
    # （实测 rino_41：L5「末を」y67-80 x77-103 与 L6「見逃すな(?)」y67-80 x150-235
    #  是同一行，y 完全相同、x 不同）。若各段独立绘制，译文会互相压住。
    # 判据：y 区间重叠超过一半、且 x 区间不重叠 -> 视为同一行
    groups: list[dict] = []
    for b in sorted(targets, key=lambda x: (x["y1"], x["x1"])):
        placed = False
        for g in groups:
            oy = min(g["y2"], b["y2"]) - max(g["y1"], b["y1"])
            min_h = min(g["y2"] - g["y1"], b["y2"] - b["y1"]) + 1
            if oy > min_h * 0.5 and b["x1"] >= g["x2"]:
                g["text"] += b["_zh"]
                g["x2"] = max(g["x2"], b["x2"])
                g["y2"] = max(g["y2"], b["y2"])
                g["idx"] += "+" + b["idx"]
                placed = True
                break
        if not placed:
            groups.append({"idx": b["idx"], "y1": b["y1"], "y2": b["y2"],
                           "x1": b["x1"], "x2": b["x2"], "text": b["_zh"],
                           "bg": b["_bg"], "fg": b["_fg"]})

    # 统一字号：以各行高的中位数为基准。
    #   - 下限 14px：单行对话画面若按 0.8 折算会偏小（实测 12px 视觉上明显偏小）
    #   - 上限：墨迹高不超过中位行高，避免多行 SNS 排版互相碰撞
    heights = sorted(g["y2"] - g["y1"] + 1 for g in groups)
    med_h = heights[len(heights) // 2] if heights else 14
    font_size = max(14, int(med_h * 0.9))
    while font_size > 8:
        _f = load_font(font_size)
        _bb = _f.getbbox("测")
        if _bb[3] - _bb[1] <= med_h:
            break
        font_size -= 1

    for g in groups:
        zh = g["text"]
        lf = g.get("fg", g_fg)          # 该行自己的文字色
        lb = g.get("bg", g_bg)
        # 碰撞避让：译文常比原文宽，按 x 起点直接画会压到左侧头像。
        # 从基准字号起逐级缩小，直到「不与已有内容重叠」。
        placed = False
        size = font_size
        while size >= 8:
            f = load_font(size)
            bb = f.getbbox(zh)
            ink_w = bb[2] - bb[0]          # 真实墨迹宽度
            ink_h = bb[3] - bb[1]
            off = bb[1]          # d.text 的 y 是字形顶部，墨迹顶还要往下 off 像素
            y_draw = g["y1"] - off
            # 碰撞检查用**墨迹宽度**而不是 advance 宽度（bb[2]）。
            # advance 含字形左右边距，比墨迹宽，会让检查误判"有内容"而把字号压到 9px
            # （实测 partner\kankei_ayano.png：16px 被压成 9px，文字几乎看不见）。
            # 同时只检查文字盒右侧 40px 内，不把远处的箭头/图标算进来。
            probe_right = min(W - 1, g["x1"] + ink_w + 8)
            if (g["x1"] + ink_w <= W - 1
                    and not occupied(g["x1"], y_draw, probe_right, y_draw + ink_h, lb)):
                d.text((g["x1"], y_draw), zh, font=f, fill=lf)
                placed = True
                break
            size -= 1

        if not placed:
            # 仍放不下：水平压缩到行宽内（保持行高）
            f = load_font(max(8, font_size - 2))
            bb = f.getbbox(zh)
            ink_h = bb[3] - bb[1]
            txt_w = bb[2] - bb[0]
            avail = max(20, min(W - 1, g["x2"]) - g["x1"])
            tmp = Image.new("RGBA", (txt_w + 4, ink_h + 8), (0, 0, 0, 0))
            td = ImageDraw.Draw(tmp)
            td.text((2, 4 - bb[1]), zh, font=f, fill=lf + (255,))
            tmp = tmp.resize((avail, ink_h + 8), Image.LANCZOS)
            base.alpha_composite(tmp, (g["x1"], g["y1"]))
        n += 1

    out_png.parent.mkdir(parents=True, exist_ok=True)
    base.save(out_png)
    return len(boxes), n


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--boxes", required=True)
    ap.add_argument("--map", required=True, help="JSON: {\"L2\": \"译文\", ...}")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bg", help="底色 RRGGBB（默认自动探测）")
    ap.add_argument("--text-color", help="文字色 RRGGBB")
    a = ap.parse_args()
    tr = json.loads(Path(a.map).read_text(encoding="utf-8"))
    total, done = render_lines(tr, Path(a.src), Path(a.boxes), Path(a.out), a.bg, a.text_color)
    print("行数 %d，替换 %d 行 -> %s" % (total, done, a.out))
