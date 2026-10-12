# 批量带坐标 OCR：遍历目录，每张图输出一个 "### 文件名" 段
# 用法: powershell -File ocr-boxes-batch.ps1 -Dir <目录> -Out <汇总txt> [-Lang ja-JP]
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Lang = "ja-JP"
)
$ErrorActionPreference = "Stop"
$tmpDir = Join-Path $env:TEMP ("ocrbox_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
$script = Join-Path $PSScriptRoot "ocr-boxes.ps1"

$files = Get-ChildItem -LiteralPath $Dir -File -Filter *.png | Sort-Object Name
$all = New-Object System.Collections.Generic.List[string]
$totalLines = 0
foreach ($f in $files) {
    $one = Join-Path $tmpDir ($f.BaseName + ".boxes.txt")
    & $script -Path $f.FullName -Out $one -Lang $Lang | Out-Null
    $all.Add("### " + $f.Name)
    if (Test-Path $one) {
        foreach ($l in [System.IO.File]::ReadAllLines($one, [System.Text.Encoding]::UTF8)) {
            $all.Add($l)
            if ($l -match '^L\d') { $totalLines++ }
        }
    }
    $all.Add("")
    Write-Host ("[ok] {0}" -f $f.Name)
}
[System.IO.File]::WriteAllLines($Out, $all, (New-Object System.Text.UTF8Encoding($false)))
Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ("files={0} lines={1} -> {2}" -f $files.Count, $totalLines, $Out)
