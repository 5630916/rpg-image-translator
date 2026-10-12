# -*- coding: utf-8 -*-
"""
一键流水线：图片目录 -> OCR -> 中文译文 -> 译文图

用法：
  python run-all.py --root 笔记\\images --out-root 译文图
  python run-all.py --dir  某目录 --out-dir 译文图\\某主题 --lang ja-JP
  python run-all.py --root ... --skip-ocr      # OCR 已跑过，只补翻译与渲染

行为：
  - 每个子目录视为一个主题，OCR 产物写到 <主题>\\_ocr_<主题>.txt
  - 默认按 OCR 文本自动判定中文/日文（含假名即判日文），失败回退手动 --lang
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from winshell import POWERSHELL, ocr_script          # noqa: E402

OCR_PS1 = ocr_script("win-ocr.ps1")
PIPELINE = HERE / "pipeline.py"
PS51 = POWERSHELL          # 兼容旧名；实际路径由 winshell 解析，不硬编码
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
HIRA = re.compile(r"[\u3040-\u309f]")
KATA = re.compile(r"[\u30a0-\u30ff]")
CJK = re.compile(r"[\u4e00-\u9fff]")
# 语言错配时 OCR 会产出逐字乱码；这些字符在合法日语里极罕见，是错配信号
RARE_KANA = re.compile(r"[\u30a0\u30fc\u30f6]")
# 合法日语里常见的功能字（错配乱码几乎不出现这些组合）
JA_MARKERS = ("の", "は", "を", "に", "て", "で", "す", "ます", "した", "して")
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def list_images(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXT)


def run_ocr(img_dir: Path, out_txt: Path, lang: str) -> bool:
    """调用 win-ocr.ps1。stdout/stderr 重定向到文件，避免管道被沙箱限制。"""
    log = out_txt.with_suffix(".log")
    cmd = [str(PS51), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(OCR_PS1), "-Path", str(img_dir), "-Lang", lang,
           "-Out", str(out_txt), "-Mode", "full"]
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
    return proc.returncode == 0 and out_txt.is_file()


def ocr_body(ocr_txt: Path) -> str:
    text = ocr_txt.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"^###.*$", "", text, flags=re.M)


def plausibility(ocr_txt: Path) -> dict:
    """判断 OCR 结果是否像一段合法语言，而不是引擎错配产生的乱码。

    依据：合法日语必有平假名（语法助词），错配乱码会堆生僻片假名且几乎无平假名。
    """
    body = ocr_body(ocr_txt)
    cjk = len(CJK.findall(body))
    hira = len(HIRA.findall(body))
    kata = len(KATA.findall(body))
    rare = len(RARE_KANA.findall(body))
    markers = sum(body.count(m) for m in JA_MARKERS)
    cjk_dense = cjk >= 20
    # 无平假名却有大量 CJK -> 很可能是中文引擎读日文图产生的乱码
    looks_garbled = cjk_dense and hira < max(3, cjk * 0.05) and rare >= 3
    looks_japanese = hira >= 5 and markers >= 2
    return {
        "cjk": cjk, "hira": hira, "kata": kata, "rare": rare, "markers": markers,
        "garbled": looks_garbled, "japanese": looks_japanese,
    }


def ocr_both(img_dir: Path, out_txt: Path, primary: str | None) -> str:
    """两个引擎都跑，按「是否像合法语言」择优；返回最终使用的语言标签。"""
    cands = [primary] if primary else ["zh-Hans-CN", "ja-JP"]
    results: list[tuple[str, Path, dict]] = []
    for lang in cands:
        tmp = out_txt.with_name(out_txt.stem + "_" + lang.split("-")[0] + ".txt")
        if run_ocr(img_dir, tmp, lang):
            st = plausibility(tmp)
            results.append((lang, tmp, st))
            print("    %-12s CJK=%d 平假名=%d 片假名=%d 生僻=%d 日语助词=%d %s"
                  % (lang, st["cjk"], st["hira"], st["kata"], st["rare"], st["markers"],
                     "【疑似乱码】" if st["garbled"] else ("【像日语】" if st["japanese"] else "")))
    if not results:
        return primary or "zh-Hans-CN"

    def score(item):
        lang, _, st = item
        s = st["cjk"]
        if st["garbled"]:
            s -= 1000                      # 错配乱码：一票否决
        if lang.startswith("ja"):
            s += st["hira"] * 2 + st["markers"] * 3   # 日语引擎产出日语特征则加分
        return s

    results.sort(key=score, reverse=True)
    best_lang, best_tmp, _ = results[0]
    out_txt.write_bytes(best_tmp.read_bytes())
    for lang, tmp, _ in results:
        if tmp != best_tmp:
            tmp.unlink(missing_ok=True)
    return best_lang


def detect_lang(ocr_txt: Path) -> str:
    """按 OCR 结果判断原图语言；供 --skip-ocr 路径使用。"""
    st = plausibility(ocr_txt)
    if st["japanese"] or (st["hira"] >= 5 and not st["garbled"]):
        return "ja-JP"
    return "zh-Hans-CN"


def run_pipeline(ocr_txt: Path, out_dir: Path, extra: list[str]) -> bool:
    cmd = [sys.executable, str(PIPELINE), "--ocr", str(ocr_txt), "--out-dir", str(out_dir)] + extra
    proc = subprocess.run(cmd)
    return proc.returncode == 0


def process_one(img_dir: Path, out_dir: Path, lang: str | None, skip_ocr: bool,
                extra: list[str]) -> None:
    imgs = list_images(img_dir)
    if not imgs:
        print("跳过 %s：没有图片" % img_dir)
        return
    ocr_txt = img_dir / ("_ocr_%s.txt" % img_dir.name)

    if not skip_ocr or not ocr_txt.is_file():
        print("[OCR] %s  (%d 张)" % (img_dir.name, len(imgs)))
        used_lang = ocr_both(img_dir, ocr_txt, lang)
        print("  -> 采用语言: %s" % used_lang)
    else:
        used_lang = lang or detect_lang(ocr_txt)

    print("[翻译+渲染] %s -> %s  (语言 %s)" % (ocr_txt.name, out_dir, used_lang))
    if not run_pipeline(ocr_txt, out_dir, extra):
        print("  流水线失败")


def guard_paths(img_root: Path | None, img_dir: Path | None, out_root: Path | None,
                out_dir: Path | None) -> None:
    """防止把产物写进输入目录或中文笔记目录，避免污染源图与笔记。"""
    ins = [p.resolve() for p in (img_root, img_dir) if p]
    outs = [p.resolve() for p in (out_root, out_dir) if p]
    for o in outs:
        for i in ins:
            if o == i or i in o.parents:
                sys.exit("[路径防呆] 输出目录位于输入目录内部：\n  输入 %s\n  输出 %s\n"
                         "请把译文图输出到与源图分开的目录。" % (i, o))
        if "笔记" in o.parts:
            sys.exit("[路径防呆] 输出目录位于「笔记」目录内：%s\n"
                     "笔记是中文资料，日翻中的产物请放到独立目录（如 译图\\）。" % o)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="含多个主题子目录的根目录")
    ap.add_argument("--out-root", default="译文图")
    ap.add_argument("--dir", help="单个图片目录")
    ap.add_argument("--out-dir", help="单目录模式下的输出目录")
    ap.add_argument("--lang", help="强制 OCR 语言（默认自动判定：zh-Hans-CN / ja-JP）")
    ap.add_argument("--skip-ocr", action="store_true", help="跳过 OCR，复用已有 _ocr_*.txt")
    ap.add_argument("--model", default=None, help="覆盖翻译模型")
    ap.add_argument("--limit", type=int, default=0, help="每主题只处理前 N 条（试跑）")
    args = ap.parse_args()

    extra: list[str] = []
    if args.model:
        extra += ["--model", args.model]
    if args.limit:
        extra += ["--limit", str(args.limit)]

    guard_paths(Path(args.root) if args.root else None,
                Path(args.dir) if args.dir else None,
                Path(args.out_root) if args.root else None,
                Path(args.out_dir) if (args.dir and args.out_dir) else None)

    if args.dir:
        process_one(Path(args.dir), Path(args.out_dir or ("译文图/" + Path(args.dir).name)),
                    args.lang, args.skip_ocr, extra)
    elif args.root:
        root = Path(args.root)
        for sub in sorted(p for p in root.iterdir() if p.is_dir()):
            process_one(sub, Path(args.out_root) / sub.name, args.lang, args.skip_ocr, extra)
    else:
        ap.error("需要 --root 或 --dir")

    print("\n完成。译文图位于 %s" % (args.out_root if args.root else args.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
