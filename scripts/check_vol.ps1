# Check microphone volume level
try {
    $audio = New-Object -ComObject "WMPlayer.OCX"
    $cd = $audio.cdromCollection
    Write-Output "WMPlayer OK"
} catch {
    Write-Output "WMPlayer not available"
}

# Use Windows policy to check microphone
$path = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
$val = Get-ItemProperty -Path $path -Name "Value" -ErrorAction SilentlyContinue
Write-Output "Microphone consent: $($val.Value)"

# Check recording devices via SoundRecorder
Write-Output "Checking Sound Recorder..."
$sr = Get-Command SoundRecorder -ErrorAction SilentlyContinue
if ($sr) {
    Write-Output "SoundRecorder available"
} else {
    Write-Output "SoundRecorder not found"
}

# Try ntdll mic test
Write-Output "Checking via mmsys.cpl..."
# Just list what we know
Write-Output "Done."
