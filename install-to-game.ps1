# 把翻译好的加密件替换进游戏目录
#
# 用法（不传参数 = 处理配置里列出的全部批次）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File install-to-game.ps1
#   ... -File install-to-game.ps1 -CheckOnly          # 只预检，不写入
#   ... -File install-to-game.ps1 -Batch ayano_sns    # 只装指定批次
#   ... -File install-to-game.ps1 -Backup             # 替换前把游戏原件另存一份
#   ... -File install-to-game.ps1 -Game "D:\别的游戏"  # 覆盖游戏根目录
#
# 行为：
#   1. 逐个批次核对源目录（加密件）与目标目录（游戏 img\pictures\<名>）
#   2. 逐文件检查：源文件存在、目标位置存在同名原件（避免把文件放进错的目录）
#   3. 有任一检查失败就中止，不做部分替换
#   4. 全部通过后打印摘要，确认后才复制
#
# ┌──────────────────────────────────────────────────────────────┐
# │  需要改的只有下面这一段：改成你自己的游戏路径与批次目录名      │
# └──────────────────────────────────────────────────────────────┘

param(
    [string]$Game = "C:\BaiduNetdiskDownload\game\2609141538\2609141538\2609141538\NTRレRPG オチカノ",
    [string]$SrcRoot = "",
    [string[]]$Batch = @(),
    [switch]$CheckOnly,
    [switch]$Backup,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

# 批次根目录：默认为本脚本同级的 译图\
if ([string]::IsNullOrWhiteSpace($SrcRoot)) {
    $SrcRoot = Join-Path $PSScriptRoot "译图"
}
$Pics = Join-Path $Game "img\pictures"

# 批次名（工作目录） -> 游戏目录名。同名时两边写一样即可。
# 「游戏A」是历史遗留的目录名，实际对应游戏的 sino_rime_ntr。
$Map = [ordered]@{
    "游戏A"          = "sino_rime_ntr"
    "sino_rime"      = "sino_rime"
    "shino_sns"      = "shino_sns"
    "shino_sns_ntr"  = "shino_sns_ntr"
    "rino_sns"       = "rino_sns"
    "riino_sns_ntr"  = "riino_sns_ntr"
    "rino_rime"      = "rino_rime"
    "rino_rime_ntr"  = "rino_rime_ntr"
    "ayano_sns"      = "ayano_sns"
    "ayano_sns_ntr"  = "ayano_sns_ntr"
    "ayano_rime"     = "ayano_rime"
    "ayano_rime_ntr" = "ayano_rime_ntr"
    "sonota_rime"    = "sonota_rime"
    "partner"        = "partner"
}

if ($Batch.Count -gt 0) {
    $unknown = $Batch | Where-Object { -not $Map.Contains($_) }
    if ($unknown) { Write-Host "未知批次: $($unknown -join ', ')" -ForegroundColor Red; exit 2 }
    $keys = $Batch
} else {
    $keys = $Map.Keys
}

Write-Host "游戏根目录: $Game"
Write-Host "来源根目录: $SrcRoot"
Write-Host ("准备处理 {0} 个批次" -f $keys.Count)
Write-Host ""

# ---------- 预检 ----------
$plan = @()
$errors = @()
foreach ($k in $keys) {
    $target = $Map[$k]
    $srcDir = Join-Path $SrcRoot "$k\加密件"
    $dstDir = Join-Path $Pics $target

    if (-not (Test-Path -LiteralPath $SrcRoot)) { $errors += "来源根目录不存在: $SrcRoot"; break }
    if (-not (Test-Path -LiteralPath $Game))    { $errors += "游戏目录不存在: $Game"; break }
    if (-not (Test-Path -LiteralPath $srcDir))  { $errors += "[$k] 加密件目录不存在: $srcDir"; continue }
    if (-not (Test-Path -LiteralPath $dstDir))  { $errors += "[$k] 游戏目标目录不存在: $dstDir"; continue }

    $files = Get-ChildItem -LiteralPath $srcDir -File -Filter *.png_
    if ($files.Count -eq 0) { $errors += "[$k] 加密件为空: $srcDir"; continue }

    $missing = @()
    foreach ($f in $files) {
        if (-not (Test-Path -LiteralPath (Join-Path $dstDir $f.Name))) { $missing += $f.Name }
    }
    if ($missing.Count -gt 0) {
        $errors += ("[{0}] 有 {1} 个文件在游戏目录里找不到同名原件（可能装错目录）: {2}" -f `
                    $k, $missing.Count, (($missing | Select-Object -First 3) -join ', '))
        continue
    }

    $gameCount = (Get-ChildItem -LiteralPath $dstDir -File -Filter *.png_).Count
    $plan += [pscustomobject]@{
        批次 = $k; 游戏目录 = $target; 替换 = $files.Count; 游戏现有 = $gameCount
    }
}

Write-Host "==== 预检结果 ===="
$plan | Format-Table -AutoSize
$total = ($plan | Measure-Object -Property 替换 -Sum).Sum
Write-Host ("待替换合计: {0} 个文件" -f $total)

if ($errors.Count -gt 0) {
    Write-Host ""
    Write-Host "==== 发现 $($errors.Count) 个问题，已中止（未做任何修改）====" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}
Write-Host "预检通过。" -ForegroundColor Green

if ($CheckOnly) { Write-Host "-CheckOnly 已指定，不写入。"; exit 0 }

# ---------- 备份（可选） ----------
if ($Backup) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $bdir = Join-Path $SrcRoot "_游戏原件备份_$stamp"
    New-Item -ItemType Directory -Path $bdir -Force | Out-Null
    foreach ($p in $plan) {
        $dstDir = Join-Path $Pics $p.游戏目录
        $sub = Join-Path $bdir $p.游戏目录
        New-Item -ItemType Directory -Path $sub -Force | Out-Null
        $files = Get-ChildItem -LiteralPath (Join-Path $SrcRoot "$($p.批次)\加密件") -File -Filter *.png_
        foreach ($f in $files) {
            Copy-Item -LiteralPath (Join-Path $dstDir $f.Name) -Destination $sub -Force
        }
    }
    Write-Host "已备份游戏原件到: $bdir" -ForegroundColor Yellow
}

# ---------- 确认 ----------
if (-not $Yes) {
    $ans = Read-Host "确认把这 $total 个文件替换进游戏目录？输入 y 继续"
    if ($ans -ne "y") { Write-Host "已取消。"; exit 0 }
}

# ---------- 执行 ----------
$done = 0
foreach ($p in $plan) {
    $dstDir = Join-Path $Pics $p.游戏目录
    $srcDir = Join-Path $SrcRoot "$($p.批次)\加密件"
    $n = 0
    foreach ($f in Get-ChildItem -LiteralPath $srcDir -File -Filter *.png_) {
        Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $dstDir $f.Name) -Force
        $n++
    }
    $done += $n
    Write-Host ("  [{0}] -> {1}  替换 {2} 个" -f $p.批次, $p.游戏目录, $n)
}
Write-Host ""
Write-Host "完成：共替换 $done 个文件。" -ForegroundColor Green
