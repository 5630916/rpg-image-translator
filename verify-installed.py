# -*- coding: utf-8 -*-
r"""
安装后校验：确认游戏目录里的 .png_ 解密后与批次 成品\ 逐字节一致。

用法：
  python verify-installed.py --game "D:\游戏根目录"
  python verify-installed.py --game "D:\游戏" --batch-root .\译图 --pics img\pictures
  python verify-installed.py --game "D:\游戏" --map "游戏A=sino_rime_ntr"

批次名与游戏内目录名默认**同名**（例如批次 `ayano_sns` 对应
`<游戏>\img\pictures\ayano_sns\`）。名字不一致时用 --map 显式指定，
可重复使用：--map 批次=游戏目录。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rpg_decrypt import decrypt, key_from_system_json      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="校验游戏内已安装的加密件与批次成品一致")
    ap.add_argument("--game", required=True, help="游戏根目录")
    ap.add_argument("--batch-root", default=str(HERE / "译图"),
                    help=r"批次根目录，默认 <本脚本>\译图")
    ap.add_argument("--pics", default=r"img\pictures",
                    help=r"游戏内的图片目录（相对游戏根），默认 img\pictures")
    ap.add_argument("--map", action="append", default=[],
                    metavar="批次=游戏目录",
                    help="批次名与游戏目录名不一致时显式映射，可重复")
    args = ap.parse_args()

    game = Path(args.game)
    if not game.is_dir():
        print("游戏根目录不存在: %s" % game)
        return 2
    src_root = Path(args.batch_root)
    if not src_root.is_dir():
        print("批次根目录不存在: %s" % src_root)
        return 2
    pics = game / args.pics
    if not pics.is_dir():
        print("游戏图片目录不存在: %s" % pics)
        return 2

    override = {}
    for m in args.map:
        if "=" not in m:
            print("--map 格式应为 批次=游戏目录，收到: %s" % m)
            return 2
        k, v = m.split("=", 1)
        override[k.strip()] = v.strip()

    # 自动发现批次：含 加密件\ 的子目录
    batches = [d for d in sorted(src_root.iterdir())
               if d.is_dir() and not d.name.startswith("_") and (d / "加密件").is_dir()]
    if not batches:
        print(r"没有找到任何含 加密件\ 的批次（批次根：%s）" % src_root)
        return 2

    key = key_from_system_json(game)
    tot_ok = tot_bad = tot_missing = 0
    print("%-18s %-18s %7s %8s %8s" % ("批次", "游戏目录", "安装数", "解密一致", "不一致"))
    for b in batches:
        target = override.get(b.name, b.name)
        dst_dir = pics / target
        enc_dir = b / "加密件"
        prod_dir = b / "成品"
        if not dst_dir.is_dir():
            print("%-18s %-18s %7s %8s %8s  <== 游戏目录不存在" %
                  (b.name, target, "-", "-", "-"))
            tot_missing += 1
            continue
        ok = bad = 0
        missing = []
        for e in sorted(enc_dir.glob("*.png_")):
            dst = dst_dir / e.name
            png = prod_dir / (e.stem + ".png")
            if not dst.is_file():
                missing.append(e.name)
                continue
            if not png.is_file():
                continue
            if decrypt(dst.read_bytes(), key) == png.read_bytes():
                ok += 1
            else:
                bad += 1
        n = len(list(enc_dir.glob("*.png_")))
        note = ""
        if bad:
            note = "  <== 有 %d 个不一致" % bad
        elif missing:
            note = "  <== 游戏内缺 %d 个" % len(missing)
        print("%-18s %-18s %7d %8d %8d%s" % (b.name, target, n, ok, bad, note))
        if missing:
            print("     缺失示例: %s" % ", ".join(missing[:3]))
        tot_ok += ok
        tot_bad += bad
        tot_missing += len(missing)

    print()
    print("合计：解密一致 %d，不一致 %d，游戏内缺失 %d" % (tot_ok, tot_bad, tot_missing))
    good = (tot_bad == 0 and tot_missing == 0)
    print("结论: %s" % ("全部一致，安装成功" if good else "存在问题，需排查"))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
