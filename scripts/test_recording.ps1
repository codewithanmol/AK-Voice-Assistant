Write-Output "Testing microphone recording via PowerShell..."

# Try using .NET to record audio
Add-Type -AssemblyName System.Windows.Forms

# Method 1: Use MCI commands
try {
    [System.Windows.Forms.SendKeys]::SendWait('')
    Write-Output "Windows Forms loaded OK"
} catch {
    Write-Output "Windows Forms error: $_"
}

# Method 2: Use Win32 API directly
$code = @'
using System;
using System.Runtime.InteropServices;
using System.IO;

public class MicTest
{
    [DllImport("winmm.dll", SetLastError = true)]
    public static extern int waveInGetNumDevs();

    public static int GetMicCount()
    {
        return waveInGetNumDevs();
    }
}
'@

try {
    Add-Type -TypeDefinition $code -Language CSharp
    $count = [MicTest]::GetMicCount()
    Write-Output "Windows wave input devices: $count"
} catch {
    Write-Output "Could not load C# code: $_"
}

Write-Output "`nDone. If count > 0, microphone is available at Windows API level."
