# -*- coding: utf-8 -*-
"""
环境自检：确认这台 Windows 机器能跑本流水线。

用法：
  python envcheck.py
  python envcheck.py --lang ja-JP          # 只检查指定语言
  python envcheck.py --encoding-test       # 附带实测一次 OCR（需临时图）

逐项检查 Python 版本、依赖（PIL/numpy）、Windows OCR 引擎与语言包、
字体、PowerShell、API key，最后给结论。缺哪项都直接说明怎么补。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

OK = "[OK]  "
BAD = "[缺失]"
WARN = "[注意]"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print("%s %-28s %s" % (OK if ok else BAD, name, detail))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", action="append", default=None,
                    help="要检查的 OCR 语言标签，可重复；默认 ja-JP 与 ja")
    ap.add_argument("--encoding-test", action="store_true",
                    help="额外做一次真实 OCR 往返测试")
    args = ap.parse_args()

    print("=" * 62)
    print("rpg-image-translator 环境自检")
    print("=" * 62)

    # ---- 平台 ----
    is_win = sys.platform == "win32"
    check("操作系统", is_win, "%s%s" % (sys.platform, "" if is_win else "  ← 本流程仅支持 Windows"))

    # ---- Python ----
    v = sys.version_info
    check("Python 版本", v >= (3, 10), "%d.%d.%d" % (v.major, v.minor, v.micro))

    # ---- 依赖 ----
    try:
        import PIL
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        check("Pillow", True, "PIL %s" % PIL.__version__)
    except Exception as e:                                    # noqa: BLE001
        check("Pillow", False, str(e))
    try:
        import numpy
        check("numpy", True, "numpy %s" % numpy.__version__)
    except Exception as e:                                    # noqa: BLE001
        check("numpy", False, str(e))

    # ---- 字体 ----
    font_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
    regular = font_dir / "msyh.ttc"
    bold = font_dir / "msyhbd.ttc"
    check("微软雅黑 msyh.ttc", regular.is_file(), str(regular))
    check("微软雅黑粗体 msyhbd.ttc", bold.is_file(),
          "%s%s" % (bold, "" if bold.is_file() else "  ← 缺粗体时会回退到常规体"))

    # ---- PowerShell（经由 winshell 统一解析）----
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from winshell import POWERSHELL, SCRIPTS
        ps = POWERSHELL
        check("Windows PowerShell", True, ps)
    except Exception as e:                                    # noqa: BLE001
        ps = None
        check("Windows PowerShell", False, str(e).splitlines()[0])

    # ---- 随附脚本完整性（克隆后最常踩：文件没下全）----
    for rel in ("scripts/win-ocr.ps1", "scripts/ocr-boxes.ps1",
                "scripts/ocr-boxes-batch.ps1", "pipeline.py", "render_lines.py"):
        p = Path(__file__).resolve().parent / rel
        check("随附文件 %s" % rel, p.is_file(), "" if p.is_file() else "缺少该文件")

    # ---- Windows OCR 语言 ----
    if is_win and ps:
        langs = args.lang or ["ja-JP", "ja"]
        script = (
            "[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime];"
            "$m = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages;"
            "$m | ForEach-Object { $_.LanguageTag }"
        )
        try:
            out = subprocess.run([ps, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                  "-Command", script],
                                 capture_output=True, text=True, timeout=60)
            available = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
            if not available:
                check("Windows OCR 引擎", False,
                      "查不到任何识别语言；确认已安装「日语」语言包")
            else:
                check("Windows OCR 引擎", True, "已装语言: %s" % ", ".join(available))
                for tag in langs:
                    hit = tag in available or (tag.split("-")[0] in available)
                    note = "" if hit else "  ← 需要在「设置 → 时间和语言 → 语言」添加该语言（含手写/OCR）"
                    check("OCR 语言 %s" % tag, hit, "%s%s" % ("可用" if hit else "不可用", note))
        except Exception as e:                                # noqa: BLE001
            check("Windows OCR 引擎", False, "调用失败: %s" % e)

    # ---- API key ----
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    key_file = Path.home() / ".dsh" / "deepseek-api-key"
    if env_key:
        check("DeepSeek API key", True, "来自环境变量 DEEPSEEK_API_KEY（长度 %d）" % len(env_key))
    elif key_file.is_file():
        t = key_file.read_text(encoding="utf-8").strip()
        check("DeepSeek API key", bool(t), "来自 %s（长度 %d）" % (key_file, len(t)))
    else:
        check("DeepSeek API key", False,
              "请设置环境变量 DEEPSEEK_API_KEY，或写入 %s" % key_file)

    # ---- 可选：真实 OCR 往返 ----
    if args.encoding_test and is_win and ps:
        print("-" * 62)
        print("实测 OCR 往返 ……")
        try:
            from PIL import Image, ImageDraw, ImageFont
            tmp = Path(os.environ.get("TEMP", ".")) / "_rpgtrans_ocrtest.png"
            im = Image.new("RGB", (240, 60), (255, 255, 255))
            d = ImageDraw.Draw(im)
            f = ImageFont.truetype(str(regular), 28) if regular.is_file() else None
            d.text((8, 10), "テスト", font=f, fill=(0, 0, 0))
            im.save(tmp)
            ocr = Path(__file__).resolve().parent / "win-ocr.ps1"
            out = tmp.with_suffix(".txt")
            subprocess.run([ps, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(ocr), "-Path", str(tmp), "-Lang", "ja-JP",
                            "-Out", str(out), "-Mode", "full"],
                           capture_output=True, text=True, timeout=120)
            got = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
            hit = "テスト" in got or " test" in got.lower()
            check("OCR 往返测试", hit, "识别结果: %r" % got.strip()[:60])
            for p in (tmp, out):
                try:
                    p.unlink()
                except OSError:
                    pass
        except Exception as e:                                # noqa: BLE001
            check("OCR 往返测试", False, str(e))

    # ---- 结论 ----
    print("=" * 62)
    failed = [n for n, ok, _ in results if not ok]
    hard = [n for n in failed if n not in ("微软雅黑粗体 msyhbd.ttc",)]
    if not hard:
        print("结论: 环境就绪，可以开始翻译。")
        return 0
    print("结论: 有 %d 项需要处理 -> %s" % (len(hard), ", ".join(hard)))
    print("      按上面每项的提示补齐后重跑本脚本。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
