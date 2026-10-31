$ErrorActionPreference = "Stop"
$pw = (& python "C:\Users\YOUR_USERNAME\path\to\project\security\showpwd.py").Trim()
$b64 = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes("ak:$pw"))
$body = '{"text":"Radhe Radhe boss, model warm ho gaya, tumhari aawaz me bol rahi hun."}'
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:4097/api/tts" -Method Post -Body $body -ContentType "application/json" -Headers @{Authorization="Basic $b64"} -TimeoutSec 590 -UseBasicParsing
    "AUTH_TTS_OK HTTP=$($r.StatusCode) wav_bytes=$($r.RawContentLength)"
} catch {
    "AUTH_TTS_ERR: $($_.Exception.Message)"
} | Set-Content -LiteralPath "C:\Users\YOUR_USERNAME\path\to\project\tts_test_result.txt"