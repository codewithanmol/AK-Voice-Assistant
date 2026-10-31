Add-Type -AssemblyName System.Windows.Forms

$code = @"
using System;
using System.Runtime.InteropServices;

public class AudioApi
{
    [DllImport("winmm.dll")]
    public static extern int waveInGetNumDevs();

    [DllImport("winmm.dll")]
    public static extern int waveInOpen(out IntPtr hwi, int uDeviceID,
        IntPtr lpFormat, IntPtr dwCallback, IntPtr dwInstance, int fdwOpen);

    [DllImport("winmm.dll")]
    public static extern int waveInClose(IntPtr hwi);

    [DllImport("winmm.dll")]
    public static extern int waveInPrepareHeader(IntPtr hwi, IntPtr lpWaveInHdr, int uSize);

    [DllImport("winmm.dll")]
    public static extern int waveInAddBuffer(IntPtr hwi, IntPtr lpWaveInHdr, int uSize);

    [DllImport("winmm.dll")]
    public static extern int waveInStart(IntPtr hwi);

    [DllImport("winmm.dll")]
    public static extern int waveInStop(IntPtr hwi);

    [DllImport("winmm.dll")]
    public static extern int waveInReset(IntPtr hwi);

    public static int GetNumDevs() { return waveInGetNumDevs(); }
}
"@

Add-Type -TypeDefinition $code -Language CSharp
$count = [AudioApi]::GetNumDevs()
Write-Output "Audio input devices found: $count"

if ($count -gt 0) {
    Write-Output "`nMicrophone is available at the Windows API level."
    Write-Output "This confirms the hardware is accessible."
    Write-Output "`nNext step: Check Windows privacy settings and mic mute state."
}
