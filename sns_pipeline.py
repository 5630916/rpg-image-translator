# -*- coding: utf-8 -*-
"""
SNS/论坛排版图的批量流水线：逐行 OCR 坐标 -> 逐行翻译 -> 原位替换 -> 加密。

与 pipeline.py 的分工：
  pipeline.py     对话气泡（整段居中，无坐标概念）
  本脚本          多行 UI 排版（每行独立坐标，逐行原位替换）

用法：
  python sns_pipeline.py --dir 译图/shino_sns --game "<游戏根目录>" [--limit N] [--skip-ocr]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from render_lines import render_lines, parse_boxes          # noqa: E402
from pipeline import load_key, cache_load, cache_save, cache_key, DEFAULT_MODEL  # noqa: E402

from winshell import POWERSHELL, ocr_script          # noqa: E402

API_URL = "https://api.deepseek.com/chat/completions"
PS51 = POWERSHELL          # 兼容旧名；实际路径由 winshell 解析，不硬编码
OCR_PS1 = ocr_script("ocr-boxes-batch.ps1")

# 术语表：统一人名/专有名词，避免同一角色在不同条目里译法不一致
# 注意：模型的旧译文里出现过「里乃」「莉诺」等不一致写法，
# 因此这里既列正确写法，也把**错误写法本身**列为键，让旧译文被覆盖重翻。
#
# 角色对照（用户确认）：
#   詩乃  -> 诗乃   目录 sino_*  / shino_sns*
#   裡乃  -> 里乃   目录 rino_*  / riino_*     ← 注意原文用的是「裡」(U+88E1)
#   綾乃  -> 绫乃   目录 ayano_*
# 陷阱：SNS 截图里会**同时出现多个角色**（如 rino_sns 里既有
# 「胡挑澤綾乃 gkurumizawa_ayano」又有「詩乃」），所以不能按目录名推断，
# 必须按**日文原文里的名字**分别翻译。
GLOSSARY = {
    # 诗乃
    "詩乃": "诗乃", "シノ": "诗乃", "shino": "Shino", "詩乃ちゃん": "诗乃酱",
    # 里乃（rino）—— 原文常见「裡乃」「リノ」
    "裡乃": "里乃", "里乃": "里乃", "リノ": "里乃", "りの": "里乃",
    "裡乃ちゃん": "里乃酱", "裡乃姉": "里乃姐",
    "莉诺": "里乃", "里诺": "里乃", "莉乃": "里乃", "理乃": "里乃",
    # 绫乃（ayano）—— 原文「綾乃」
    "綾乃": "绫乃", "あやの": "绫乃", "アヤノ": "绫乃", "綾乃ちゃん": "绫乃酱",
    # 胡桃泽家（统一为简体；OCR 常把「澤」误读成「挑」）
    "胡桃澤": "胡桃泽", "胡桃泽": "胡桃泽", "胡挑澤": "胡桃泽", "胡挑泽": "胡桃泽",
    # 其它固定译名
    "和佳": "和佳", "りのひ": "りのひ", "りのび": "りのび",
}
SYS_PROMPT = (
    "你是日译中译者。用户给出一张游戏截图里逐行提取的文本，请你**逐行**翻译成简体中文。"
    "只输出编号与译文，格式为 @@n@@ 译文，一行一条，不要合并、不要增删、不要解释。"
    "专有名词按以下对照翻译：" + "、".join("%s→%s" % (k, v) for k, v in GLOSSARY.items()) + "。"
    "昵称、@用户名、纯编号等保持原样。"
)
MARK_RE = re.compile(r"@@\s*(\d+)\s*@@")
MAX_CHARS = 4000


def is_id_line(text: str) -> bool:
    """判断是否为无需翻译的 ID / 编号 / 图标文本"""
    s = re.sub(r"\s", "", text)
    if not s:
        return True
    if re.fullmatch(r"[@#]?[A-Za-z0-9_@#.:\-]{3,}", s):
        return True
    if re.fullmatch(r"[\d\s:@#._\-]+", s):
        return True
    # 汉字+假名少于 2 个 -> 视为符号/图标
    if len(re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]", text)) < 2:
        return True
    return False


CJK_RANGE = r"\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"

# 行首的孤立标点：原图的项目符号「・」常被 OCR 读成 。或 ()，会被模型原样搬进译文
# （实测 partner：`・抱きしめると安心する` -> `。抱紧的话会感到安心。`）
LEAD_PUNCT = re.compile(r"^[\s。、，,．.·・:：;；()（）「」『』\[\]【】\-–—_/／|｜+＋*＊~〜]+")
# 行内孤立的空括号：`( )` / `（）` 这种没有内容的，也是项目符号误读
EMPTY_PAREN = re.compile(r"[（(]\s*[)）]")


def clean(t: str) -> str:
    """清洗 OCR 行文本。

    关键：必须剥离 OCR 给 CJK 逐字插入的空格（`日 暇 だ` -> `日暇だ`），
    否则缓存键与 pipeline.py 不一致，会导致**全量重翻**（实测白花 13 万 token）。
    """
    t = re.sub(r"(?<=[%s])\s+(?=[%s])" % (CJK_RANGE, CJK_RANGE), "", t)
    t = re.sub(r"[\s\u3000]+", " ", t).strip()
    t = LEAD_PUNCT.sub("", t)          # 去行首项目符号误读
    t = EMPTY_PAREN.sub("", t)         # 去空括号
    t = re.sub(r"^[\s。、，,．.·・:：;；()（）「」『』]+", "", t)
    return re.sub(r"[・·]", "…", t).strip()


def translate_batch(items: list[str], key: str, model: str, retries: int = 3):
    numbered = "\n".join("@@%d@@ %s" % (i + 1, t) for i, t in enumerate(items))
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYS_PROMPT},
                     {"role": "user", "content": numbered}],
        "temperature": 0.2, "stream": False,
    }, ensure_ascii=False).encode("utf-8")

    last = None
    for a in range(retries):
        req = urllib.request.Request(
            API_URL, data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read().decode("utf-8"))
            content = d["choices"][0]["message"]["content"]
            parts = MARK_RE.split(content)
            got = {}
            for i in range(1, len(parts) - 1, 2):
                try:
                    got[int(parts[i])] = parts[i + 1].strip()
                except ValueError:
                    pass
            return got, d.get("usage", {})
        except urllib.error.HTTPError as e:
            last = "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:200])
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (a + 1)); continue
            break
        except Exception as e:  # noqa: BLE001
            last = str(e); time.sleep(2 * (a + 1))
    raise RuntimeError("批量翻译失败: %s" % last)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="工作目录（含 原图\\）")
    ap.add_argument("--game", help="游戏根目录（给了就顺便加密）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-ocr", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch", type=int, default=20, help="每次请求翻译多少行")
    ap.add_argument("--bg", default="FFFFFF", help="底色（SNS 图通常白底）")
    ap.add_argument("--text-color", default="000000")
    a = ap.parse_args()

    work = Path(a.dir)
    src_dir = work / "原图"
    flat_dir = work / "_工作" / "白底"
    boxes_file = work / "_工作" / "boxes_all.txt"
    out_dir = work / "成品"
    for d in (flat_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1) 合成白底 + 坐标 OCR
    if not a.skip_ocr or not boxes_file.is_file():
        print("[1/4] 合成白底")
        for p in sorted(src_dir.glob("*.png")):
            im = Image.open(p).convert("RGBA")
            Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im) \
                 .convert("RGB").save(flat_dir / p.name)
        print("[2/4] 批量坐标 OCR")
        cmd = [str(PS51), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(OCR_PS1), "-Dir", str(flat_dir), "-Out", str(boxes_file), "-Lang", "ja-JP"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print("[1/4][2/4] 跳过 OCR（复用 %s）" % boxes_file.name)

    # 2) 解析 boxes_all.txt -> 每文件的行
    blocks: dict[str, list[str]] = {}
    cur = None
    for line in boxes_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("### "):
            cur = line[4:].strip(); blocks[cur] = []
        elif line.startswith("L") and cur:
            blocks[cur].append(line)
    if a.limit:
        blocks = dict(list(blocks.items())[: a.limit])
    print("      文件 %d 个" % len(blocks))

    key = load_key()
    cache = cache_load()
    stats = {"lines": 0, "skip": 0, "cache": 0, "api": 0, "ptok": 0, "ctok": 0}

    print("[3/4] 逐行翻译")
    for fname, raw_lines in blocks.items():
        rows = parse_boxes_lines(raw_lines)
        todo = []
        for r in rows:
            stats["lines"] += 1
            t = clean(r["text"])
            if is_id_line(t):
                stats["skip"] += 1
                continue
            r["clean"] = t
            ck = cache_key(a.model, t)
            if ck in cache:
                r["zh"] = cache[ck]; stats["cache"] += 1
            else:
                r["ck"] = ck
                todo.append(r)

        # 分批翻译
        for i in range(0, len(todo), a.batch):
            chunk = todo[i:i + a.batch]
            got, usage = {}, {}
            try:
                got, usage = translate_batch([r["clean"] for r in chunk], key, a.model)
            except Exception as e:  # noqa: BLE001
                print("      [WARN] %s 批量失败: %s" % (fname, e))
            stats["api"] += 1
            stats["ptok"] += usage.get("prompt_tokens", 0)
            stats["ctok"] += usage.get("completion_tokens", 0)
            for j, r in enumerate(chunk, start=1):
                zh = got.get(j)
                if not zh:
                    print("      [缺] %s %s: %s" % (fname, r["idx"], r["clean"][:30]))
                    continue
                r["zh"] = zh
                cache[r["ck"]] = zh
            cache_save(cache)

        # 渲染
        tr = {r["idx"]: r["zh"] for r in rows if r.get("zh")}
        if tr:
            src = src_dir / fname
            render_lines(tr, src, _boxes_path_of(raw_lines, fname), out_dir / fname,
                         a.bg, a.text_color)
            print("      %-16s 替换 %2d 行 / 共 %2d 行" % (fname, len(tr), len(rows)))

    # 3) 加密
    if a.game:
        print("[4/4] 回加密")
        enc = work / "加密件"
        enc.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(HERE / "rpg_decrypt.py"), "--encrypt",
               "--dir", str(out_dir), "--out", str(enc), "--key-from", a.game]
        subprocess.run(cmd, stdout=subprocess.DEVNULL)

    print("\n==== 汇总 ====")
    print("行数 %d（跳过 %d，缓存命中 %d）" % (stats["lines"], stats["skip"], stats["cache"]))
    tot = stats["ptok"] + stats["ctok"]
    print("API token：prompt=%d completion=%d total=%d" % (stats["ptok"], stats["ctok"], tot))
    return 0


def parse_boxes_lines(raw_lines: list[str]) -> list[dict]:
    """把 "L1\\ty=.. x=..\\t文本" 解析成 dict 列表"""
    out = []
    for raw in raw_lines:
        parts = raw.split("\t", 2)
        if len(parts) != 3:
            continue
        idx, geo, text = parts[0].strip(), parts[1], parts[2].strip()
        try:
            yseg, xseg = geo.split(" x=")
            y1, y2 = (int(v) for v in yseg.replace("y=", "").split(".."))
            x1, x2 = (int(v) for v in xseg.split(" h=")[0].split(".."))
        except Exception:
            continue
        out.append({"idx": idx, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "text": text})
    return out


_BOX_CACHE: dict[str, Path] = {}


def _boxes_path_of(raw_lines: list[str], fname: str) -> Path:
    """render_lines.parse_boxes 需要文件路径；这里把单文件行写成临时文件并缓存。"""
    p = _BOX_CACHE.get(fname)
    if p is None:
        p = Path(os.environ.get("TEMP", ".")) / ("snsbox_" + fname + ".txt")
        p.write_text("\n".join(raw_lines), encoding="utf-8")
        _BOX_CACHE[fname] = p
    return p


if __name__ == "__main__":
    sys.exit(main())
