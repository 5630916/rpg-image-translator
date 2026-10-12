# 带坐标的 OCR：输出每行的包围盒 + 逐词坐标
# 用法: powershell -File ocr-boxes.ps1 -Path <图片> -Out <输出txt> [-Lang ja-JP]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Lang = "ja-JP"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction SilentlyContinue
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]
function Await($op, $t) {
    $m = $asTaskGeneric.MakeGenericMethod($t)
    $x = $m.Invoke($null, @($op)); $x.Wait(-1) | Out-Null; $x.Result
}

$langType = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$langObj = [Activator]::CreateInstance($langType, @($Lang))
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($langObj)
if ($null -eq $eng) { Write-Error "无该语言 OCR 引擎: $Lang"; exit 2 }

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])

$lines = New-Object System.Collections.Generic.List[string]
$idx = 0
foreach ($line in $res.Lines) {
    $idx++
    $minX = [double]::MaxValue; $minY = [double]::MaxValue
    $maxX = [double]::MinValue; $maxY = [double]::MinValue
    $words = New-Object System.Collections.Generic.List[string]
    foreach ($w in $line.Words) {
        $r = $w.BoundingRect
        $x1 = [double]$r.X; $y1 = [double]$r.Y
        $x2 = $x1 + [double]$r.Width; $y2 = $y1 + [double]$r.Height
        if ($x1 -lt $minX) { $minX = $x1 }
        if ($y1 -lt $minY) { $minY = $y1 }
        if ($x2 -gt $maxX) { $maxX = $x2 }
        if ($y2 -gt $maxY) { $maxY = $y2 }
        $words.Add(("{0}|{1:N0},{2:N0},{3:N0},{4:N0}" -f $w.Text, $x1, $y1, ($x2 - $x1), ($y2 - $y1)))
    }
    $lines.Add(("L{0}`ty={1:N0}..{2:N0} x={3:N0}..{4:N0} h={5:N0}`t{6}" -f `
                $idx, $minY, $maxY, $minX, $maxX, ($maxY - $minY), $line.Text))
    $lines.Add(("   words: " + ($words -join " ")))
}
[System.IO.File]::WriteAllLines($Out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ("lines={0} -> {1}" -f $idx, $Out)
