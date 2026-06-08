param(
    [int]$Port = 8799,
    [string]$HostName = '127.0.0.1',
    [int]$TimeoutSec = 120,
    [int]$IntervalSec = 2
)

$ErrorActionPreference = 'SilentlyContinue'
$url = "http://${HostName}:$Port/"
$deadline = (Get-Date).AddSeconds($TimeoutSec)

while ((Get-Date) -lt $deadline) {
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(1000)) {
            $client.EndConnect($iar) | Out-Null
            $client.Close()
            Start-Process $url | Out-Null
            exit 0
        }
    } catch {
    } finally {
        if ($client) {
            $client.Close()
        }
    }
    Start-Sleep -Seconds $IntervalSec
}

Start-Process $url | Out-Null
