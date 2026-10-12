# -*- coding: utf-8 -*-
"""
图片 -> OCR 文本 -> 中文译文 -> 译文图  流水线（T2：翻译在脚本侧完成，不消耗会话 token）

用法示例：
  # 1) 只跑 OCR（PowerShell 侧，WinRT）
  powershell -NoProfile -ExecutionPolicy Bypass -File win-ocr.ps1 -Path <图片目录> -Out ocr.txt -Mode full

  # 2) 翻译 + 渲染（本脚本）
  python pipeline.py --ocr _ocr/work.txt --out-dir 译文图/作品名
  python pipeline.py --root 笔记/images --out-root 译文图 --recursive   # 批量按主题

依赖：仅标准库 + Pillow
密钥：优先环境变量 DEEPSEEK_API_KEY，其次 ~/.dsh/deepseek-api-key
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------- 常量 ----------------
API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-flash"
KEY_FILE = Path.home() / ".dsh" / "deepseek-api-key"
CACHE_FILE = Path.home() / ".dsh" / "translation-cache.json"

FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"]
WIDTH = 603
PAD_X = 18
PAD_Y = 14
BG = (255, 255, 255)
FG = (26, 26, 26)
LINE_GAP = 9
FONT_SIZE = 16
TARGET_LINES = 3
MAX_LINES = 5            # 放不下时允许放宽到这么多行
CJK = r"\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
NO_START = set("，。；：、！？）》」』%…")
NO_END = set("（《「『")
GAP = 3                    # 多行之间的额外行距
_LAST_META: dict = {}      # 最近一次渲染的元信息（字号等），供调用方打印

SYSTEM_PROMPT = (
    # 实测：显式抑制推理可把 reasoning token 从 7536 降到 1404（约 1/5），
    # 耗时 28.9s -> 7.5s，且译文更完整。翻译不需要思维链。
    "直接输出简体中文译文，不要推理，不要输出思考过程。"
    "若原文已是中文则原样返回。专有名词保留常用写法。"
)

# 批量翻译：一次请求翻多条，用来摊薄推理 token（实测每张 2106 -> 126 token）
BATCH_SYSTEM_PROMPT = (
    "你是专业译者。用户给出以 @@n@@ 开头的多条原文，逐条翻译成简体中文。"
    "严格按相同编号逐条输出，格式为 @@n@@ 译文，一条一行；"
    "不要合并、不要漏条、不要输出解释或任何额外文字。"
    "专有名词保留常用写法；若某条原文已是中文，做最小限度通顺化后原样输出。"
)
BATCH_MARK_RE = re.compile(r"@@\s*(\d+)\s*@@")
BATCH_MAX_CHARS = 6000      # 单批原文总字符上限（防上下文过长）



# ---------------- 密钥 ----------------
def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    if KEY_FILE.is_file():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    sys.exit("找不到 API key：请设置 DEEPSEEK_API_KEY 或写入 %s" % KEY_FILE)


# ---------------- OCR 产物解析 ----------------
HEADER_RE = re.compile(r"^###\s+(?P<name>.+?)\s*(?:\(|\[).*$")

# 讲义类图片的固定页眉/页脚：每张都重复，翻译前剔除可省 token、也让译文更干净
BOILERPLATE = [
    re.compile(r"版权所有[：:].{0,40}"),
    re.compile(r"tpandme@163\.com", re.I),
    re.compile(r"^\s*[Ww][Ee][Ll][Cc][Oo][Mm][Ee]\s+[Tt][Oo].*$", re.M),
    re.compile(r"Ctrl\s*\+\s*F\d+", re.I),
]


def clean_ocr_text(text: str) -> str:
    """去掉页眉页脚与明显的装饰性乱码，减少无效翻译量。"""
    t = strip_cjk_spaces(text)
    for pat in BOILERPLATE:
        t = pat.sub(" ", t)
    # 标题之前通常是 logo/装饰误识别（如 "囗回回回回 0 T"）；从课程标题开始保留
    m = re.search(r"唐老狮系列教程|第\s*\d+\s*[章节讲]|^\s*[A-Za-z]", t)
    if m and m.start() > 0:
        t = t[m.start():]
    # 压缩空白与连续标点噪声
    t = re.sub(r"[\s\u3000]+", " ", t).strip()
    t = re.sub(r"[回囗]{2,}", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


# 翻译前剥离 OCR 噪声：这些符号是 OCR 对省略号/装饰的误读，
# 留在文本里会被模型原样搬进译文（实测出现「・别在意哦」「所以・」等）
OCR_NOISE = re.compile(r"[・·ᐧ‧∙]")
# 行首的行内分隔符（原图是「・気にしないでね」这类项目符号，译文中应去掉）
LEAD_MARK = re.compile(r"^[\s、,，。.\-–—]+")


def sanitize_ja(text: str) -> str:
    """把 OCR 文本里的噪声符号清掉，得到更干净的待翻译原文。"""
    t = OCR_NOISE.sub("…", text)          # 半角/全角间隔号 -> 省略号
    t = re.sub(r"…{2,}", "…", t)            # 连续省略号压成一个
    t = t.strip()
    t = LEAD_MARK.sub("", t)                # 去掉行首的悬挂标点
    return t.strip()


def strip_cjk_spaces(text: str) -> str:
    return re.sub(r"(?<=[%s])\s+(?=[%s])" % (CJK, CJK), "", text)


def parse_ocr(path: Path) -> list[tuple[str, str]]:
    """解析 win-ocr.ps1 产物 -> [(图片名, 文本), ...]"""
    out: list[tuple[str, str]] = []
    name, buf = None, []

    def flush():
        if name is not None:
            txt = clean_ocr_text("".join(buf)) if any(buf) else ""
            if txt:
                out.append((name, txt))

    for raw in path.read_text(encoding="utf-8").splitlines():
        m = HEADER_RE.match(raw.strip())
        if m:
            flush()
            name, buf = m.group("name").strip(), []
            continue
        if name is not None:
            buf.append(raw + "\n")
    flush()
    return out


# ---------------- 翻译 ----------------
def cache_load() -> dict:
    if CACHE_FILE.is_file():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def cache_save(c: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")


def cache_key(model: str, text: str) -> str:
    return hashlib.sha256((model + "\x00" + text).encode("utf-8")).hexdigest()[:32]


def translate(text: str, key: str, model: str, timeout: int = 180, retries: int = 3) -> str:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.2,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")

    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            API_URL, data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip(), data.get("usage", {})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last = "HTTP %s: %s" % (e.code, detail)
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("翻译失败: %s" % last)


def translate_batch(texts: list[str], key: str, model: str,
                    timeout: int = 300, retries: int = 3) -> tuple[dict[int, str], dict]:
    """一次请求翻译多条，返回 ({序号(1基): 译文}, usage)。序号对不上时由调用方回退单条。"""
    numbered = "\n".join("@@%d@@ %s" % (i + 1, t) for i, t in enumerate(texts))
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": numbered},
        ],
        "temperature": 0.2,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")

    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            API_URL, data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            parts = BATCH_MARK_RE.split(content)
            got: dict[int, str] = {}
            for i in range(1, len(parts) - 1, 2):
                try:
                    idx = int(parts[i])
                except ValueError:
                    continue
                got[idx] = parts[i + 1].strip()
            return got, data.get("usage", {})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last = "HTTP %s: %s" % (e.code, detail)
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("批量翻译失败: %s" % last)


# ---------------- 渲染 ----------------
def load_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONTS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    raise RuntimeError("no CJK font")


def layout(font: ImageFont.FreeTypeFont, text: str, k: int, max_w: int):
    n = len(text)
    if n == 0:
        return None
    cw = [font.getbbox(c)[2] - font.getbbox(c)[0] for c in text]
    total = sum(cw)
    if total > max_w * k:
        return None
    ideal = total / k
    INF = float("inf")
    dp = [[INF] * (k + 1) for _ in range(n + 1)]
    prev = [[-1] * (k + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for t in range(1, k + 1):
        for i in range(1, n + 1):
            w = 0
            for j in range(i - 1, -1, -1):
                w += cw[j]
                if w > max_w:
                    break
                if dp[j][t - 1] == INF:
                    continue
                seg = text[j:i]
                pen = 0.0
                if seg[0] in NO_START:
                    pen += 1e9
                if seg[-1] in NO_END:
                    pen += 1e9
                cost = dp[j][t - 1] + (w - ideal) ** 2 + pen
                if cost < dp[i][t]:
                    dp[i][t] = cost
                    prev[i][t] = j
    if dp[n][k] == INF:
        return None
    segs, i, t = [], n, k
    while t > 0:
        j = prev[i][t]
        if j < 0:
            return None
        segs.append(text[j:i])
        i, t = j, t - 1
    segs.reverse()
    return segs


def pick_font(text: str, max_w: int, max_lines: int = MAX_LINES):
    """按文本长度选字号：短文本用大字，长文自动缩小。返回 (font, k) 或 None。"""
    n = len(text)
    if n <= 120:
        sizes = [FONT_SIZE, 15, 14]
    elif n <= 260:
        sizes = [15, 14, 13, 12]
    elif n <= 450:
        sizes = [13, 12, 11, 10]
    else:
        sizes = [11, 10, 9, 8]
    for size in sizes:
        font = load_font(size)
        for k in range(TARGET_LINES, max_lines + 1):
            if layout(font, text, k, max_w):
                return font, size, k
    return None


def greedy_wrap(font, text: str, max_w: int) -> list[str]:
    """贪心填满：每行尽可能放满（短语得到 1 行，长文自然折行）。

    遵守避头尾：标点不置行首。
    """
    lines, cur = [], ""
    for ch in text:
        if not cur or font.getbbox(cur + ch)[2] - font.getbbox(cur + ch)[0] <= max_w:
            cur += ch
            continue
        # 需要断行：若下一个字符是禁则行首标点，先并进本行
        if ch in NO_START:
            cur += ch
            continue
        lines.append(cur)
        cur = ch
    if cur:
        lines.append(cur)
    return lines


def detect_box(img: "Image.Image"):
    """分析源图，返回 (底色, 框包围盒, 原文文字包围盒, 文字颜色)。

    - 底色 = 不透明区里最常见的颜色（对话框底色，白底或彩色气泡）
    - 文字包围盒 = 「不透明且非底色」像素的包围盒；无则为 None
    - 文字颜色 = 非底色像素里最多的颜色（白底→深灰字，蓝底→白字）
    """
    from collections import Counter
    w, h = img.size
    px = img.load()
    colors = Counter()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 128:
                colors[(r, g, b)] += 1
    if not colors:
        return None, None, None, None
    main = colors.most_common(1)[0][0]

    xs, ys, txs, tys = [], [], [], []
    textc = Counter()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 128:
                xs.append(x)
                ys.append(y)
                if (r, g, b) != main:
                    txs.append(x)
                    tys.append(y)
                    textc[(r, g, b)] += 1
    box = (min(xs), min(ys), max(xs), max(ys))
    tbox = (min(txs), min(tys), max(txs), max(tys)) if txs else None
    fg = textc.most_common(1)[0][0] if textc else None
    return main, box, tbox, fg


def render_fit_source(text: str, out_png: Path, src_size: tuple[int, int],
                      src_mode: str = "RGBA", src_path: Path | None = None,
                      keep_box: bool = False,
                      match_source_ink: bool = False,
                      text_color: str | None = None) -> tuple[int, int, int]:
    """输出与源图**同尺寸、同颜色模式**，文字水平垂直居中。返回 (行数, 高, 宽)。

    keep_box=True（需 src_path）：以原图为底，只抹掉原文字、保留原有透明背景
    与圆角对话框，再把新文字居中画进对话框内。
    match_source_ink=True：按**原文墨迹高度**反推字号，让中文大小贴近原日文
    （默认 False，则按对话框可容纳的最大字号）。
    """
    text = text.strip()
    tw, th = src_size

    base = None
    box = None
    main = None
    src_text_h = None
    fg = FG
    if keep_box and src_path and Path(src_path).is_file():
        with Image.open(src_path) as im0:
            base = im0.convert("RGBA").copy()
        if base.size != (tw, th):
            base = base.resize((tw, th))
        main, box, tbox, src_fg = detect_box(base)
        if tbox:
            src_text_h = tbox[3] - tbox[1] + 1        # 原文墨迹高度
        if src_fg:
            fg = src_fg                               # 沿用原图文字颜色（白底深字 / 蓝底白字）
    if text_color:
        tc = text_color.strip().lstrip("#")
        if len(tc) == 6:
            try:
                fg = tuple(int(tc[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                pass

    # 可用范围：有框时用框内（留 6px 内边距），否则用整张画布
    if box:
        bx0, by0, bx1, by1 = box
        avail_w = max(20, (bx1 - bx0 + 1) - 12)
        avail_h = max(10, (by1 - by0 + 1) - 4)
    else:
        avail_w = max(20, tw - PAD_X * 2)
        avail_h = max(10, th - PAD_Y * 2)

    sizes = (32, 28, 24, 22, 20, 18, 16, 15, 14, 13, 12, 11, 10, 9, 8)

    def line_metrics(f):
        ink = f.getbbox("测")
        return ink[3] - ink[1], ink[1]      # (墨迹高, 首行偏移)

    def fits(size: int):
        f = load_font(size)
        ls = greedy_wrap(f, text, avail_w)
        ink_h, off = line_metrics(f)
        total = (ink_h + GAP) * len(ls) - GAP + off
        return f, ls, ink_h, off, total

    chosen = None
    if match_source_ink and src_text_h:
        # 取「墨迹高不超过原文墨迹高」的最大字号，再确保能放进框
        best = None
        for size in sizes:
            f, ls, ink_h, off, total = fits(size)
            if ink_h <= src_text_h and total <= avail_h:
                best = (size, f, ls, ink_h, off, total)
                break
        if best is None:
            # 没有更小的合适字号时，退到「能放进框」的最大字号
            for size in sizes:
                f, ls, ink_h, off, total = fits(size)
                if total <= avail_h:
                    best = (size, f, ls, ink_h, off, total)
                    break
        chosen = best

    if chosen is None:
        for size in sizes:
            f, ls, ink_h, off, total = fits(size)
            if total <= avail_h:
                chosen = (size, f, ls, ink_h, off, total)
                break

    if chosen is None:                      # 最小字号仍放不下 -> 截断
        size = sizes[-1]
        font = load_font(size)
        lines, cur = [], ""
        for ch in text:
            if font.getbbox(cur + ch)[2] - font.getbbox(cur + ch)[0] <= avail_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        ink_h, off = line_metrics(font)
        max_lines = max(1, (avail_h - off + GAP) // (ink_h + GAP))
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = (lines[-1][:-1] + "…") if len(lines[-1]) > 1 else "…"
        chosen = (size, font, lines, ink_h, off, 0)

    size, font, lines, ink_h, off, total = chosen
    lh = ink_h + GAP
    total = lh * len(lines) - GAP + off

    if base is not None and box:
        img = base
        px = img.load()
        bx0, by0, bx1, by1 = box
        # 抹掉原文字：框内非主色像素填回主色
        for y in range(by0, by1 + 1):
            for x in range(bx0, bx1 + 1):
                r, g, b, a = px[x, y]
                if a > 128 and (r, g, b) != main:
                    px[x, y] = main + (255,)
        y = by0 + max(0, ((by1 - by0 + 1) - total) // 2)
        d = ImageDraw.Draw(img)
        for ln in lines:
            bb = font.getbbox(ln)
            lw = bb[2] - bb[0]
            d.text((bx0 + ((bx1 - bx0 + 1) - lw) // 2, y), ln, font=font, fill=fg)
            y += lh
    else:
        mode = "RGBA" if src_mode == "RGBA" else "RGB"
        bg = (255, 255, 255, 255) if mode == "RGBA" else (255, 255, 255)
        img = Image.new(mode, (tw, th), bg)
        d = ImageDraw.Draw(img)
        y = max(0, (th - total) // 2)
        for ln in lines:
            bb = font.getbbox(ln)
            lw = bb[2] - bb[0]
            d.text(((tw - lw) // 2, y), ln, font=font, fill=FG)
            y += lh

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    _LAST_META["size"] = size
    _LAST_META["src_ink_h"] = src_text_h
    _LAST_META["ink_h"] = ink_h
    return len(lines), img.height, img.width


def render(text: str, out_png: Path, max_w: int | None = None,
           auto_width: bool = False, scale: float = 1.0) -> tuple[int, int, int]:
    """渲染译文卡。返回 (行数, 高, 实际宽度)。

    - 优先贪心填满（短语一行放得下就一行）
    - 放不下时依次降字号，最多 MAX_LINES 行
    - auto_width=True 时，卡片宽度贴合文字实际宽度
    """
    text = text.strip()
    base_w = max_w if max_w else WIDTH - PAD_X * 2

    def fit(size: int):
        font = load_font(size)
        lines = greedy_wrap(font, text, base_w)
        return font, lines

    # 选第一个能放进 MAX_LINES 行的字号
    chosen = None
    for size in (16, 15, 14, 13, 12, 11, 10, 9, 8):
        font, lines = fit(size)
        if len(lines) <= MAX_LINES:
            chosen = (font, lines, size)
            break
    if chosen is None:
        font, lines = fit(8)
        chosen = (font, lines, 8)
    font, lines, size = chosen

    text_w = max((font.getbbox(l)[2] - font.getbbox(l)[0]) for l in lines)
    line_h = size + LINE_GAP
    height = PAD_Y * 2 + line_h * len(lines) - LINE_GAP

    if auto_width:
        width = text_w + PAD_X * 2
        if scale != 1.0:
            width = int(width * scale)
            height = int(height * scale)
        img = Image.new("RGB", (width, height), BG)
        d = ImageDraw.Draw(img)
        for i, ln in enumerate(lines):
            d.text((PAD_X, PAD_Y + i * line_h), ln, font=font, fill=FG)
    else:
        width = WIDTH
        img = Image.new("RGB", (width, height), BG)
        d = ImageDraw.Draw(img)
        for i, ln in enumerate(lines):
            d.text((PAD_X, PAD_Y + i * line_h), ln, font=font, fill=FG)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return len(lines), img.height, img.width


# ---------------- 主流程 ----------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr", help="win-ocr.ps1 的产物文本")
    ap.add_argument("--out-dir", help="输出目录（与 --ocr 搭配）")
    ap.add_argument("--root", help="批量模式：图片根目录，其每个子目录视为一个主题")
    ap.add_argument("--out-root", default="译文图", help="批量模式的输出根目录")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（试跑用）")
    ap.add_argument("--auto-width", action="store_true",
                    help="卡片宽度贴合文字（短语不会拖一大片空白）")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="整体缩放倍数，配合 --auto-width 使用，如 1.5 / 2")
    ap.add_argument("--fit-source", metavar="DIR",
                    help="输出与 DIR 中同名源图**同尺寸、同颜色模式**，文字居中"
                         "（DIR 为解密后的原图目录）")
    ap.add_argument("--keep-box", action="store_true",
                    help="配合 --fit-source：以原图为底，保留透明背景与圆角对话框，"
                         "只抹掉原文字再居中写新文字（否则整张画布填白）")
    ap.add_argument("--match-source-ink", action="store_true",
                    help="配合 --keep-box：按**原文墨迹高度**反推字号，"
                         "让译文大小贴近原日文（默认按对话框可容纳的最大字号）")
    ap.add_argument("--text-color", metavar="RRGGBB",
                    help="覆盖文字颜色（如 ffffff）。默认从原图提取："
                         "白底气泡取深色字、蓝底气泡取白字")
    ap.add_argument("--batch-size", type=int, default=1,
                    help="每次请求翻译多少条（默认 1=逐条）。实测批量对 token 无显著收益，"
                         "成本主要由输入长度决定，故默认逐条以保证对齐可靠")
    args = ap.parse_args()

    key = load_key()
    cache = cache_load()
    stats = {"images": 0, "cached": 0, "api": 0, "batches": 0, "fallback": 0, "ptok": 0, "ctok": 0}

    def run_group(ocr_file: Path, out_dir: Path) -> None:
        items = parse_ocr(ocr_file)
        if args.limit:
            items = items[: args.limit]
        print("\n== %s -> %s  (%d 条)" % (ocr_file.name, out_dir, len(items)))
        stats["images"] += len(items)

        src_dir = Path(args.fit_source) if args.fit_source else None

        def emit(zh: str, stem: str, tag: str) -> None:
            """按选定模式渲染一张卡"""
            out_png = out_dir / (stem + ".png")
            if src_dir:
                src = src_dir / (stem + ".png")
                if not src.is_file():
                    print("  [skip] %s：--fit-source 下找不到 %s" % (stem, src.name))
                    return
                with Image.open(src) as im:
                    size, mode = im.size, im.mode
                lines, h, w = render_fit_source(zh, out_png, size, mode,
                                                src_path=src, keep_box=args.keep_box,
                                                match_source_ink=args.match_source_ink,
                                                text_color=args.text_color)
                meta = []
                if _LAST_META.get("size"):
                    meta.append("字号%dpx" % _LAST_META["size"])
                if _LAST_META.get("src_ink_h"):
                    meta.append("原文墨迹%dpx" % _LAST_META["src_ink_h"])
                    meta.append("译文墨迹%dpx" % _LAST_META.get("ink_h", 0))
                extra = ("  [" + " ".join(meta) + "]") if meta else ""
            else:
                lines, h, w = render(zh, out_png, auto_width=args.auto_width,
                                     scale=args.scale)
                extra = ""
            print("  [%s] %s -> %s  (%d行 %dx%d)%s  %s"
                  % (tag, stem, out_png.name, lines, w, h, extra, zh[:28]))

        # 1) 先看缓存，收集待翻条目（原文先做噪声清洗，译文才不会带上「・」）
        todo: list[tuple[str, str, str]] = []      # (stem, text, cache_key)
        for name, text in items:
            stem = Path(name).stem
            clean = sanitize_ja(text)
            ck = cache_key(args.model, clean)
            if ck in cache:
                stats["cached"] += 1
                emit(cache[ck], stem, "cache")
            else:
                todo.append((stem, clean, ck))

        # 2) 批量翻译待翻条目（按条数与字符数双重分组）
        batch_size = args.batch_size
        pending = todo
        while pending:
            if batch_size <= 0:
                chunk, pending = pending[:1], pending[1:]
            else:
                chunk, chars, pending = [], 0, pending
                while pending and len(chunk) < batch_size:
                    nxt = pending[0]
                    if chunk and chars + len(nxt[1]) > BATCH_MAX_CHARS:
                        break
                    chunk.append(nxt)
                    chars += len(nxt[1])
                    pending = pending[1:]

            got: dict[int, str] = {}
            usage: dict = {}
            if len(chunk) > 1:
                try:
                    got, usage = translate_batch([t for _, t, _ in chunk], key, args.model)
                    stats["batches"] += 1
                    stats["ptok"] += usage.get("prompt_tokens", 0)
                    stats["ctok"] += usage.get("completion_tokens", 0)
                except Exception as e:  # noqa: BLE001
                    print("  [WARN] 批量失败，回退逐条：%s" % e)
                    got = {}

            for i, (stem, text, ck) in enumerate(chunk, start=1):
                zh = got.get(i)
                tag = "batch"
                if not zh:                       # 编号缺失或未走批量 -> 单条回退
                    try:
                        zh, u2 = translate(text, key, args.model)
                    except Exception as e:  # noqa: BLE001
                        print("  [FAIL] %s: %s" % (stem, e))
                        continue
                    stats["fallback"] += 1
                    stats["api"] += 1
                    stats["ptok"] += u2.get("prompt_tokens", 0)
                    stats["ctok"] += u2.get("completion_tokens", 0)
                    tag = "single"
                cache[ck] = zh
                cache_save(cache)
                emit(zh, stem, tag)

    if args.ocr:
        out_dir = Path(args.out_dir) if args.out_dir else Path("译文图") / Path(args.ocr).stem
        run_group(Path(args.ocr), out_dir)
    elif args.root:
        root = Path(args.root)
        for sub in sorted(p for p in root.iterdir() if p.is_dir()):
            ocr_file = sub / ("_ocr_" + sub.name + ".txt")
            if not ocr_file.is_file():
                print("跳过 %s：缺少 %s" % (sub.name, ocr_file.name))
                continue
            run_group(ocr_file, Path(args.out_root) / sub.name)
    else:
        ap.error("需要 --ocr 或 --root")

    print("\n==== 汇总 ====")
    print("图片 %d 张；命中缓存 %d；批量请求 %d 次；单条回退 %d 次"
          % (stats["images"], stats["cached"], stats["batches"], stats["fallback"]))
    tot = stats["ptok"] + stats["ctok"]
    print("API token：prompt=%d completion=%d total=%d" % (stats["ptok"], stats["ctok"], tot))
    n_api = stats["images"] - stats["cached"]
    if n_api > 0:
        print("平均每张：%.0f token（仅统计实际翻译的 %d 张）" % (tot / n_api, n_api))
    print("缓存：%s" % CACHE_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
