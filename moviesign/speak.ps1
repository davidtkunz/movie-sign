param(
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [string]$Voice = "",
    [int]$Rate = 0
)

# Text arrives via a file rather than an argument so that apostrophes, quotes,
# and anything else a riff might contain never have to survive shell quoting.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$text = [IO.File]::ReadAllText($TextFile, [Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer

try {
    if ($Voice) {
        $installed = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
        $match = $installed | Where-Object { $_ -eq $Voice } | Select-Object -First 1
        if (-not $match) {
            $match = $installed | Where-Object { $_ -like "*$Voice*" } | Select-Object -First 1
        }
        if ($match) {
            $synth.SelectVoice($match)
        } else {
            Write-Error "voice '$Voice' not installed. Available: $($installed -join ', ')"
        }
    }
    $synth.Rate = $Rate
    $synth.SetOutputToWaveFile($OutFile)
    $synth.Speak($text)
    $synth.SetOutputToNull()
} finally {
    $synth.Dispose()
}
