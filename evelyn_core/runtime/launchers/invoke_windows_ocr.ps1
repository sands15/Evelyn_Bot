param(
    [Parameter(Mandatory = $true)]
    [string]$ImagePath
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]

function Wait-WindowsRuntimeOperation {
    param(
        [Parameter(Mandatory = $true)]
        $Operation,
        [Parameter(Mandatory = $true)]
        [Type]$ResultType
    )

    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.IsGenericMethodDefinition -and
            $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        } |
        Select-Object -First 1
    if ($null -eq $asTask) {
        throw 'windows_ocr_as_task_unavailable'
    }
    $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}

$resolvedImage = [string](Resolve-Path -LiteralPath $ImagePath -ErrorAction Stop)
$extension = [System.IO.Path]::GetExtension($resolvedImage).ToLowerInvariant()
if ($extension -notin @('.png', '.jpg', '.jpeg', '.bmp')) {
    throw 'windows_ocr_image_type_not_allowed'
}

$stream = $null
$bitmap = $null
try {
    $file = Wait-WindowsRuntimeOperation (
        [Windows.Storage.StorageFile]::GetFileFromPathAsync($resolvedImage)
    ) ([Windows.Storage.StorageFile])
    $stream = Wait-WindowsRuntimeOperation (
        $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
    ) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Wait-WindowsRuntimeOperation (
        [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    ) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Wait-WindowsRuntimeOperation (
        $decoder.GetSoftwareBitmapAsync()
    ) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        throw 'windows_ocr_language_unavailable'
    }
    $result = Wait-WindowsRuntimeOperation (
        $engine.RecognizeAsync($bitmap)
    ) ([Windows.Media.Ocr.OcrResult])
    [ordered]@{
        schema = 'windows_ocr.result.v1'
        ok = $true
        language = [string]$engine.RecognizerLanguage.LanguageTag
        text = [string]$result.Text
    } | ConvertTo-Json -Compress
} finally {
    if ($null -ne $bitmap) {
        $bitmap.Dispose()
    }
    if ($null -ne $stream) {
        $stream.Dispose()
    }
}
