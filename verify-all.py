# -*- coding: utf-8 -*-
r"""
全批次总校验：加密格式、尺寸、透明、头像/插画保留。

用法：
  python verify-all.py --game "D:\游戏根目录"
  python verify-all.py --game "D:\游戏" --batch-root .\译图 ayano_sns rino_sns

批次目录结构约定（每个批次一个子目录）：
  <批次>\
    ├── 原图\      解密出来的原始 PNG
    ├── 成品\      渲染好的译文 PNG
    └── 加密件\    回加密后的 .png_（与游戏内文件同格式）

每张图检查四项：
  1. fake header 正确（RPG Maker MZ 加密头）
  2. 用 <游戏>\data\System.json 的密钥解密后，与 成品\ 逐字节一致
  3. 解密结果可被 Pillow 正常解码
  4. 尺寸与原图一致；左上区域（疑似头像/插画）的非透明像素未显著减少
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rpg_decrypt import decrypt, key_from_system_json, FAKE_HEADER   # noqa: E402
from PIL import Image                                                # noqa: E402


def check(batch: Path, key: bytes) -> tuple[int, int, int, int, int, int]:
    enc_dir = batch / "加密件"
    encs = sorted(enc_dir.glob("*.png_"))
    hdr = rt = okdec = badsz = lost = 0
    for e in encs:
        png = batch / "成品" / (e.stem + ".png")
        src = batch / "原图" / (e.stem + ".png")
        if not png.is_file() or not src.is_file():
            continue
        s = Image.open(src).convert("RGBA")
        o = Image.open(png).convert("RGBA")
        if s.size != o.size:
            badsz += 1
        sa, oa = np.array(s), np.array(o)
        sl = int((sa[:, :, 3] > 0)[:60, :80].sum())
        ol = int((oa[:, :, 3] > 0)[:60, :80].sum())
        if sl > 300 and ol < sl * 0.6:
            lost += 1
        d = e.read_bytes()
        if d[:16] == FAKE_HEADER:
            hdr += 1
        back = decrypt(d, key)
        if back == png.read_bytes():
            rt += 1
        try:
            Image.open(io.BytesIO(back)).load()
            okdec += 1
        except Exception:
            pass
    return len(encs), hdr, rt, okdec, badsz, lost


def main() -> int:
    ap = argparse.ArgumentParser(description="校验加密件与成品的完整性和一致性")
    ap.add_argument("--game", required=True,
                    help=r"游戏根目录（含 data\System.json），用于取加密密钥")
    ap.add_argument("--batch-root", default=str(HERE / "译图"),
                    help=r"批次根目录，默认 <本脚本>\译图")
    ap.add_argument("batch", nargs="*", help="只校验这些批次；省略则全部")
    args = ap.parse_args()

    game = Path(args.game)
    if not game.is_dir():
        print("游戏根目录不存在: %s" % game)
        return 2
    root = Path(args.batch_root)
    if not root.is_dir():
        print("批次根目录不存在: %s" % root)
        return 2

    want = set(args.batch)
    key = key_from_system_json(game)
    rows = []
    tot = dict(n=0, hdr=0, rt=0, ok=0, sz=0, lost=0)
    for batch in sorted(root.iterdir()):
        if not batch.is_dir() or batch.name.startswith("_"):
            continue
        if want and batch.name not in want:
            continue
        if not (batch / "加密件").is_dir():
            continue
        n, hdr, rt, okdec, badsz, lost = check(batch, key)
        rows.append((batch.name, n, hdr, rt, okdec, badsz, lost))
        tot["n"] += n; tot["hdr"] += hdr; tot["rt"] += rt
        tot["ok"] += okdec; tot["sz"] += badsz; tot["lost"] += lost

    if not rows:
        print(r"没有找到任何含 加密件\ 的批次目录（批次根：%s）" % root)
        return 2

    print("%-16s %7s %8s %10s %8s %7s %8s" %
          ("批次", "加密件", "header", "解密一致", "可解码", "尺寸差", "头像/插画丢"))
    for name, n, hdr, rt, okdec, badsz, lost in rows:
        print("%-16s %7d %8d %10d %8d %7d %8d" % (name, n, hdr, rt, okdec, badsz, lost))
    print()
    print("合计 %d 个加密件：header %d  解密一致 %d  可解码 %d  尺寸差 %d  头像/插画丢失 %d"
          % (tot["n"], tot["hdr"], tot["rt"], tot["ok"], tot["sz"], tot["lost"]))
    bad = (tot["hdr"] != tot["n"] or tot["rt"] != tot["n"] or tot["ok"] != tot["n"]
           or tot["sz"] or tot["lost"])
    print("结论: %s" % ("存在问题，需排查" if bad else "全部通过"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
