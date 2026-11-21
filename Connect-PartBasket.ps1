[CmdletBinding()]
param(
    [ValidatePattern('^tunnel_[a-zA-Z0-9_-]+$')]
    [string]$TunnelId
)

$ErrorActionPreference = 'Stop'
$clientPath = Join-Path $PSScriptRoot 'build\openai-tunnel-v0.0.21-win-amd64\tunnel-client.exe'
if (-not (Test-Path -LiteralPath $clientPath -PathType Leaf)) {
    throw 'The OpenAI tunnel client is missing. See the ChatGPT plugin section of README.md.'
}

if (-not $TunnelId) {
    $TunnelId = (Read-Host 'OpenAI tunnel ID').Trim()
    if ($TunnelId -notmatch '^tunnel_[a-zA-Z0-9_-]+$') {
        throw 'Enter the tunnel ID shown in OpenAI Platform. It starts with tunnel_.'
    }
}

$processInfo = New-Object System.Diagnostics.ProcessStartInfo
$processInfo.FileName = $clientPath
$processInfo.WorkingDirectory = $PSScriptRoot
$processInfo.UseShellExecute = $false
$processInfo.Arguments = "run --control-plane.tunnel-id $TunnelId --control-plane.api-key env:CONTROL_PLANE_API_KEY --mcp.server-url http://127.0.0.1:8787/mcp --health.listen-addr 127.0.0.1:0"
foreach ($name in @($processInfo.EnvironmentVariables.Keys)) {
    if ($name -match '^(CONTROL_PLANE_|TUNNEL_CLIENT_|MCP_|HEALTH_|OPENAI_API_KEY$)') {
        $processInfo.EnvironmentVariables.Remove($name)
    }
}

Write-Host 'Leave this window open while using PartBasket in ChatGPT.'
$runtimeKey = Read-Host 'OpenAI tunnel runtime API key' -AsSecureString
$secret = [IntPtr]::Zero
try {
    if ($runtimeKey.Length -eq 0) {
        throw 'A tunnel runtime API key is required.'
    }
    $secret = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($runtimeKey)
    $processInfo.EnvironmentVariables['CONTROL_PLANE_API_KEY'] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secret)
    $tunnel = [System.Diagnostics.Process]::Start($processInfo)
} finally {
    $processInfo.EnvironmentVariables.Remove('CONTROL_PLANE_API_KEY')
    if ($secret -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secret)
    }
    $runtimeKey.Dispose()
}

try {
    $tunnel.WaitForExit()
    exit $tunnel.ExitCode
} finally {
    $tunnel.Dispose()
}
