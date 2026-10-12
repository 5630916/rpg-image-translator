# -*- coding: utf-8 -*-
"""
Windows 环境适配：定位 PowerShell 解释器与随附的 .ps1 OCR 脚本。

抽出来的原因：Windows PowerShell（5.1）的安装位置虽然基本固定，但
直接硬编码 `C:\\Windows\\...` 在少数环境（精简系统、改过 SystemRoot）下会失败；
而 PowerShell 脚本挪进 `scripts/` 后，多处路径也要跟着变。
集中在这里，改一处即可。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"


def resolve_powershell() -> str:
    """返回可用的 Windows PowerShell 可执行文件路径。

    优先 PATH 上的 `powershell`，其次 SystemRoot 下的标准位置。
    找不到就抛错，并给出明确的补救说明（而不是让后续调用莫名其妙地失败）。
    """
    for name in ("powershell", "powershell.exe"):
        found = shutil.which(name)
        if found:
            return found
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    cand = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if cand.is_file():
        return str(cand)
    raise FileNotFoundError(
        "找不到 Windows PowerShell（powershell.exe）。\n"
        "本流程的 OCR 依赖 Windows.Media.Ocr（WinRT），需要系统自带的 Windows PowerShell，\n"
        "不能用 PowerShell 7（pwsh）。请确认：\n"
        "  1) 系统为 Windows 10/11；\n"
        "  2) %%SystemRoot%%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe 存在；\n"
        "  3) 或把 powershell.exe 加入 PATH。"
    )


def ocr_script(name: str) -> Path:
    """返回 scripts/ 下某个 OCR 脚本的绝对路径（并检查存在）。"""
    p = SCRIPTS / name
    if not p.is_file():
        raise FileNotFoundError("缺少 OCR 脚本: %s" % p)
    return p


POWERSHELL = resolve_powershell()
