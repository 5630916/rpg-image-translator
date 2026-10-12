# -*- coding: utf-8 -*-
r"""
RPG Maker MZ 加密图片 双向转换工具（解密 / 回加密）

与游戏 js/rmmz_core.js 的 Utils.decryptArrayBuffer 完全一致：
    [0:16]   fake header  固定 52 50 47 4d 56 00 00 00 00 03 01 00 00 00 00 00
    [16:]    body
    body[0:16] ^= key     （key = System.json 的 encryptionKey，每 2 个十六进制字符 1 字节）
    其余 body 为明文

用法：
  # 解密（默认输出到 <输入目录>_decrypted，保留原文件）
  python rpg_decrypt.py --dir "<游戏图片目录>" --key-from "<游戏根目录>"

  # 批量：解密某个 img 下的所有子目录
  python rpg_decrypt.py --dir "<游戏根目录>\img\pictures" --key-from "<游戏根目录>" --batch

  # 回加密（从解密产出还原为游戏可读的加密文件）
  python rpg_decrypt.py --encrypt --dir "<解密目录>" --out "<输出目录>" --key-from "<游戏根目录>"

安全：绝不修改原文件；解密产物与源文件分开存放。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FAKE_HEADER = bytes.fromhex("5250474d560000000003010000000000")
ENC_SUFFIXES = {".png_", ".rpgmvp", ".jpg_", ".rpgmvo", ".ogg_", ".m4a_", ".rpgmvm"}
IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


# ---------------- 密钥 ----------------
def key_from_system_json(game_root: Path) -> bytes:
    """从游戏的 data/System.json 读取 encryptionKey。"""
    for cand in (game_root / "data" / "System.json",
                 game_root / "www" / "data" / "System.json",
                 game_root / "System.json"):
        if cand.is_file():
            raw = cand.read_bytes()
            for enc in ("utf-8-sig", "utf-8", "utf-16"):
                try:
                    sysj = json.loads(raw.decode(enc))
                    break
                except Exception:
                    sysj = None
            if not sysj:
                continue
            k = sysj.get("encryptionKey") or ""
            if not k:
                raise SystemExit("System.json 里 encryptionKey 为空：%s" % cand)
            if len(k) != 32 or any(c not in "0123456789abcdefABCDEF" for c in k):
                raise SystemExit("encryptionKey 格式异常（应为 32 位十六进制）：%r" % k)
            return bytes.fromhex(k)
    raise SystemExit("在 %s 下找不到 data/System.json" % game_root)


# ---------------- 核心算法 ----------------
def decrypt(source: bytes, key: bytes) -> bytes:
    """与 rmmz_core.js Utils.decryptArrayBuffer 等价。"""
    if source[:16] != FAKE_HEADER:
        raise ValueError("fake header 不匹配，可能不是加密文件")
    body = bytearray(source[16:])
    for i in range(min(16, len(body))):
        body[i] ^= key[i]
    return bytes(body)


def encrypt(png: bytes, key: bytes) -> bytes:
    """逆运算：前置 fake header，并异或前 16 字节。"""
    body = bytearray(png)
    for i in range(min(16, len(body))):
        body[i] ^= key[i]
    return FAKE_HEADER + bytes(body)


def verify_png(data: bytes) -> tuple[bool, str]:
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        return True, "%dx%d %s" % (im.width, im.height, im.mode)
    except Exception as e:
        return False, str(e)[:60]


# ---------------- 目录处理 ----------------
def iter_encrypted(d: Path):
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in ENC_SUFFIXES)


def out_name_for(p: Path) -> str:
    """加密名 -> 原名：去掉结尾的 `_`；.rpgmvp -> .png"""
    low = p.suffix.lower()
    if low == ".rpgmvp":
        return p.stem + ".png"
    if p.name.endswith("_"):
        return p.name[:-1]
    return p.name


def do_decrypt(src_dir: Path, out_dir: Path, key: bytes, quiet: bool = False) -> int:
    files = iter_encrypted(src_dir)
    if not files:
        print("  %s: 没有加密文件" % src_dir)
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = bad = 0
    for p in files:
        try:
            data = decrypt(p.read_bytes(), key)
        except Exception as e:
            print("  !! %s: %s" % (p.name, e))
            bad += 1
            continue
        good, info = verify_png(data)
        if not good:
            print("  !! %s 解密后无法解析: %s" % (p.name, info))
            bad += 1
            continue
        (out_dir / out_name_for(p)).write_bytes(data)
        ok += 1
        if not quiet:
            print("  [ok] %-34s %s" % (p.name, info))
    print("  -> %s：成功 %d，失败 %d" % (out_dir.name, ok, bad))
    return ok


def do_encrypt(src_dir: Path, out_dir: Path, key: bytes, quiet: bool = False) -> int:
    files = sorted(p for p in src_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in IMG_SUFFIXES)
    if not files:
        print("  %s: 没有图片" % src_dir)
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for p in files:
        enc = encrypt(p.read_bytes(), key)
        # RPG Maker MZ 的加密图片扩展名：.png -> .png_（保持与你原始文件一致）
        target = out_dir / (p.name + "_")
        target.write_bytes(enc)
        ok += 1
        if not quiet:
            print("  [enc] %-34s %d B" % (target.name, len(enc)))
    print("  -> %s：加密 %d 个" % (out_dir.name, ok))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="RPG Maker MZ 加密图片双向转换")
    ap.add_argument("--dir", required=True, help="输入目录（解密时为加密图片目录）")
    ap.add_argument("--out", help="输出目录（默认 <输入>_decrypted / <输入>_encrypted）")
    ap.add_argument("--encrypt", action="store_true", help="改为加密（PNG -> 加密文件）")
    ap.add_argument("--key-from", required=True, help="游戏根目录（含 data/System.json）")
    ap.add_argument("--batch", action="store_true", help="遍历 --dir 下所有含加密文件的子目录")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = ap.parse_args()

    key = key_from_system_json(Path(args.key_from))
    src = Path(args.dir)
    if not src.is_dir():
        raise SystemExit("输入目录不存在：%s" % src)

    mode = "加密" if args.encrypt else "解密"
    print("模式: %s" % mode)
    print("密钥: %s（来自 %s）" % (key.hex(), Path(args.key_from) / "data" / "System.json"))
    print("输入: %s" % src)

    if args.batch:
        subs = [d for d in sorted(src.iterdir()) if d.is_dir() and iter_encrypted(d)]
        if not subs:
            print("  --batch：未找到含加密文件的子目录")
        total = 0
        for sub in subs:
            out = Path(args.out) / sub.name if args.out else sub.parent / (sub.name + "_decrypted")
            if args.dry_run:
                print("  [dry] %s -> %s (%d 个)" % (sub.name, out, len(iter_encrypted(sub))))
                continue
            total += do_decrypt(sub, out, key)
        print("合计: %d" % total)
        return 0

    out = Path(args.out) if args.out else src.parent / (src.name + ("_encrypted" if args.encrypt else "_decrypted"))
    if args.dry_run:
        n = len(iter_encrypted(src)) if not args.encrypt else len(
            [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMG_SUFFIXES])
        print("  [dry] %s -> %s (%d 个)" % (src, out, n))
        return 0
    if args.encrypt:
        do_encrypt(src, out, key)
    else:
        do_decrypt(src, out, key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
