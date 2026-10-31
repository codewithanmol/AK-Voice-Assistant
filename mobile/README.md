# AK Mobile

Android companion app for the AK Voice Assistant. The phone is a thin
remote: the laptop does the thinking, the phone gives you chat, voice input,
and a live voice call over WebRTC.

The app talks to `ak_server.py` (in the repo root) over your local network or
Tailscale.

## What is in this folder

| Path | What |
|------|------|
| `AK-Mobile.apk` | Ready-to-install debug APK (built from this source) |
| `app/src/main/` | Kotlin source, resources and manifest |
| `app/build.gradle.kts` | App module configuration |
| `settings.gradle.kts`, `build.gradle.kts` | Gradle project files |
| `local.properties.example` | Template for the Android SDK path |

## Requirements

- Android 8.0 (API 26) or newer
- Python 3.10+ on the laptop (for `ak_server.py`)
- Phone and laptop on the same WiFi, or both on Tailscale
- JDK 17 + Android SDK 34 (only if you want to rebuild the APK)

## 1. Start the laptop server

```powershell
# from the repository root
pip install aiohttp aiortc   # only needed for the phone bridge / call
python ak_server.py
```

On first run the server generates a random password and saves it to
`security/server_secret.txt` (git-ignored). The console prints the address:

```
[AK server] Apna server ready: http://192.168.1.5:4097
```

You can also set it yourself:

```powershell
python ak_server.py --port 4097 --password "your-strong-password"
```

Keep this terminal open while you use the phone.

## 2. Install the APK

Copy `AK-Mobile.apk` to the phone (USB, Drive, Telegram, etc.), open it, and
allow "Install unknown apps" if Android asks.

## 3. Connect the app

1. Open the app and tap the gear icon.
2. Server URL: `http://<laptop-ip>:4097` (the address the server printed).
3. Server password: the value from the console or `security/server_secret.txt`.
4. Tap **Test connection**, then **Save**.
5. The status dot turns green when the chat is reachable.

The app pairs itself on first use and stores the device token encrypted with
the Android Keystore. The server password is never kept in plaintext.

## Rebuilding the APK

```powershell
# copy the template and point it at your SDK
Copy-Item local.properties.example local.properties
# edit sdk.dir in local.properties

$env:JAVA_HOME = "C:\path\to\jdk-17"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
gradle assembleDebug --no-daemon
# output: app/build/outputs/apk/debug/app-debug.apk
```

The Gradle wrapper is not checked in; use your own Gradle 8.9+ install.

## Server API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check |
| `/chat` | POST | `{"message": "..."}` -> `{"reply": "..."}` |
| `/api/pair` | POST | Exchange `install_id` + password for a device token |
| `/api/call/trigger` | POST | Start a WebRTC call session |
| `/call` | GET | WebRTC signalling page |

Authenticated routes accept `Authorization: Bearer <token>` (paired devices)
or HTTP Basic `ak:<password>`.

## Security notes

- The server listens on `0.0.0.0:4097` by default so the phone can reach it.
  Only run it on networks you trust.
- Set a strong password; there is no built-in default.
- Pairing from outside Tailscale requires the server password.
- For access beyond your LAN, put both devices on Tailscale instead of
  exposing the port to the internet.
- Never commit `security/`, `local.properties`, keystores, or `.env` files.

## License

MIT, same as the main project.
