# Windows 自带 OCR（Windows.Media.Ocr）批量识别脚本
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File win-ocr.ps1 -Path <图片或目录> [-Lang zh-Hans-CN] [-Out out.txt]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$Lang = "zh-Hans-CN",
    [string]$Out = "",
    [ValidateSet("full", "digest")]
    [string]$Mode = "full",
    [int]$DigestChars = 300,
    [int]$Limit = 0,
    [switch]$Recurse
)

$ErrorActionPreference = "Stop"

# WinRT 投影：必须先加载这个程序集，否则 [Windows.*] 类型不可见
$winrt = Join-Path $env:SystemRoot "System32\WinMetadata"
Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction SilentlyContinue
try {
    [void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    [void][Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
    [void][Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
}
catch {
    Write-Error "无法加载 WinRT 类型: $($_.Exception.Message)"
    exit 3
}

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($op, $resultType) {
    $m = $asTaskGeneric.MakeGenericMethod($resultType)
    $t = $m.Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    return $t.Result
}

[void][Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$langType = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$langObj = [Activator]::CreateInstance($langType, @($Lang))
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($langObj)
if ($null -eq $engine) {
    Write-Error "该语言没有 OCR 引擎: $Lang"
    exit 2
}

if (Test-Path -LiteralPath $Path -PathType Container) {
    $files = Get-ChildItem -LiteralPath $Path -File -Recurse:$Recurse |
        Where-Object { $_.Extension -match '^\.(png|jpg|jpeg|bmp|tif|tiff|webp)$' } |
        Sort-Object FullName
}
else {
    $files = @(Get-Item -LiteralPath $Path)
}

if ($Limit -gt 0) { $files = @($files | Select-Object -First $Limit) }

Write-Host ("engine: {0}   files: {1}" -f $engine.RecognizerLanguage.LanguageTag, $files.Count)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$all = New-Object System.Collections.Generic.List[string]
$totalChars = 0
$totalMs = 0

foreach ($f in $files) {
    $sw.Restart()
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($f.FullName)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $stream.Dispose()
    $sw.Stop()

    $totalMs += $sw.ElapsedMilliseconds
    $text = $result.Text
    # Windows OCR 给 CJK 逐字加空格，按需压掉
    $text = $text -replace '(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', ''
    $totalChars += $text.Length
    if ($Mode -eq "digest") {
        $head = $text.Substring(0, [Math]::Min($DigestChars, $text.Length))
        $all.Add("### " + $f.Name + "  [" + $text.Length + " chars]")
        $all.Add($head)
        $all.Add("")
    }
    else {
        $all.Add("### " + $f.Name + "  (" + $sw.ElapsedMilliseconds + " ms, " + $text.Length + " chars)")
        $all.Add($text)
        $all.Add("")
    }
    Write-Host ("[ok] {0,-56} {1,5} ms  {2,5} chars" -f $f.Name, $sw.ElapsedMilliseconds, $text.Length)
}

Write-Host ("TOTAL {0} files, {1} ms, {2} chars" -f $files.Count, $totalMs, $totalChars)

if ($Out -ne "") {
    [System.IO.File]::WriteAllLines($Out, $all, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "wrote $Out"
}
