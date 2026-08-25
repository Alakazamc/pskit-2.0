[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $RemoteCommand
)

$ErrorActionPreference = 'Stop'
$configPath = Join-Path $PSScriptRoot 'ssh_config.windows'

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "SSH config not found: $configPath"
}

$sshArgs = @('-F', $configPath, 'pskit-a6000')
if ($RemoteCommand.Count -gt 0) {
    $sshArgs += ($RemoteCommand -join ' ')
}

& ssh @sshArgs
exit $LASTEXITCODE

