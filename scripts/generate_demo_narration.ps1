param(
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\static\pos\media\oxpos-demo-narration-v3.wav')
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

$segments = @(
    @{ Text = 'It is nine forty-seven. The store is closed, but Mika is still counting every shelf by hand.'; BreakAfter = 3300 },
    @{ Text = 'And one wrong number can mean an empty shelf, a lost sale, and another late night.'; BreakAfter = 3300 },
    @{ Text = 'Then Mika finds OXPOS, and everything clicks. Checkout, stock, and reports finally work as one.'; BreakAfter = 4400 },
    @{ Text = 'Now every sale updates inventory instantly. No extra tally. No second notebook. No guesswork.'; BreakAfter = 4400 },
    @{ Text = "Low-stock alerts show what needs attention, while clear reports turn today's sales into tomorrow's decisions."; BreakAfter = 3600 },
    @{ Text = 'So Mika closes on time and grows with confidence. Stop counting the past. Start running what is next, with OXPOS.'; BreakAfter = 0 }
)

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
if (Test-Path -LiteralPath $OutputPath) {
    throw "Refusing to overwrite $OutputPath"
}

$prompt = New-Object System.Speech.Synthesis.PromptBuilder 'en-US'
$prompt.AppendBreak([TimeSpan]::FromMilliseconds(280))
foreach ($segment in $segments) {
    $prompt.AppendText($segment.Text)
    if ($segment.BreakAfter -gt 0) {
        $prompt.AppendBreak([TimeSpan]::FromMilliseconds($segment.BreakAfter))
    }
}

$synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synthesizer.SelectVoice('Microsoft Zira Desktop')
    $synthesizer.Rate = 2
    $synthesizer.Volume = 100
    $synthesizer.SetOutputToWaveFile($OutputPath)
    $synthesizer.Speak($prompt)
}
finally {
    $synthesizer.Dispose()
}

$file = Get-Item -LiteralPath $OutputPath
Write-Output "Generated $($file.FullName) ($($file.Length) bytes)"
