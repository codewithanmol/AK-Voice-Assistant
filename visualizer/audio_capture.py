"""audio_capture.py — Real-time DUAL-DIRECTION audio analysis for the Orb Visualizer.

Threading model
---------------
sounddevice calls our callback on its OWN audio thread. Each callback only does a
small, fixed amount of work (RMS + one 1024-point FFT, ~microseconds) and writes
smoothed values into a lock-protected buffer. The pygame frame loop never blocks
on audio — it just reads the latest numbers. This guarantees lock-solid 60 FPS.

Channels
--------
  * mic  -> user microphone  (USER_LISTENING / inbound voice)
  * spk  -> PC output loopback via WASAPI (ASSISTANT_SPEAKING / TTS outbound)

Both channels share the same AudioAnalyzer: raw RMS -> EMA smoothing ->
loudness-normalised 0..1, plus an FFT magnitude spectrum collapsed into
16 log-spaced bands (bass / mid / treble shortcuts for the shader-less math).
"""

import threading

import numpy as np
import sounddevice as sd

RATE = 22050          # plenty for voice/music, halves FFT cost vs 44100
CHUNK = 1024          # ~46 ms per callback
N_BANDS = 16

# EMA smoothing: fast attack on rising signal, slower release so levels ease down.
_ATTACK = 0.35
_RELEASE = 0.06


def _ema(cur, new):
    """Exponential Moving Average — cur moves frac-of-gap toward new each frame."""
    a = _ATTACK if new > cur else _RELEASE
    return cur + (new - cur) * a


class AudioAnalyzer:
    """Per-channel feature extractor: smoothed RMS + normalised log-frequency bands."""

    def __init__(self, sr=RATE, chunk=CHUNK):
        self.sr = sr
        self.nfft = chunk
        self._win = np.hanning(chunk).astype(np.float32)  # Hann -> no spectral leakage
        self._lock = threading.Lock()                     # audio thread <-> main thread
        self.rms = 0.0          # smoothed, normalised amplitude 0..1
        self.spec = np.zeros(N_BANDS, dtype=np.float32)   # normalised band magnitudes
        self.bass = 0.0
        self.mid = 0.0
        self.treble = 0.0
        self._boost = 1e-6      # slow AGC reference (mean loudness)
        self._peak = 1e-6       # slow spectrum peak for normalisation

    # -- called from sounddevice audio thread --------------------------------
    def update(self, block):
        """Analyse one PCM block (any length) and publish smoothed features."""
        block = np.asarray(block, dtype=np.float32).ravel()
        n = len(block)
        if n == 0:
            return
        nfft = min(n, self.nfft)
        rms_raw = float(np.sqrt(np.mean(block * block)))

        mags = np.abs(np.fft.rfft(block[:nfft] * self._win[:nfft]))[1:]  # drop DC bin
        total = len(mags)
        band = np.zeros(N_BANDS, dtype=np.float32)
        if total > 1:
            # log-spaced band boundaries over the FFT bins (voice-favourable spacing)
            starts = np.unique(
                np.round(np.logspace(np.log10(1), np.log10(total - 1), N_BANDS + 1))
                .astype(int)
            )
            starts = np.clip(starts, 0, total - 1)[:N_BANDS]
            counts = np.diff(np.append(starts, total))
            sums = np.add.reduceat(mags, starts)
            band = sums / np.maximum(counts, 1).astype(np.float32)

        with self._lock:
            # slow AGC so a quietly-spoken voice is still visible
            self._boost += (rms_raw - self._boost) * 0.01
            self._peak = max(self._peak * 0.997, float(band.max()))
            n_rms = rms_raw / (self._boost * 2.0 + 1e-6)
            self.rms = float(np.clip(_ema(self.rms, n_rms), 0.0, 1.4))
            self.spec = self.spec + (band / (self._peak + 1e-9) - self.spec) * 0.4
            self.spec = np.clip(self.spec, 0.0, 1.0).astype(np.float32)
            self.bass = float(self.spec[0])     # low rumble / voice fundamentals
            i_mid = max(1, N_BANDS // 3)
            self.mid = float(self.spec[i_mid])
            self.treble = float(self.spec[-1])

    def snapshot(self):
        """Thread-safe read of the latest features (non-blocking)."""
        with self._lock:
            return {
                "rms": self.rms,
                "bass": self.bass,
                "mid": self.mid,
                "treble": self.treble,
                "spec": self.spec.copy(),
            }


def find_loopback_device():
    """Find a WASAPI input that mirrors an output device (system audio / TTS loopback).

    Windows loopback devices expose the *same name* as their output port but as an
    input. We try each candidate with `WasapiSettings(loopback=True)` and return the
    first that accepts it. Returns None on failure (visualizer degrades gracefully).
    """
    try:
        devices = sd.query_devices()
        wasapi = sd.query_hostapis()[0]["index"]
        names = {
            d["name"]: i
            for i, d in enumerate(devices)
            if d["hostapi"] == wasapi and d["max_output_channels"] > 0
        }
        for i, d in enumerate(devices):
            if d["hostapi"] != wasapi or d["max_input_channels"] <= 0:
                continue
            if d["name"] not in names:
                continue  # not a mirrored output device -> not loopback
            try:
                sd.check_input_settings(
                    device=i, samplerate=RATE, channels=1,
                    extra_settings=sd.WasapiSettings(loopback=True),
                )
                return i
            except sd.PortAudioError:
                continue
    except Exception:
        pass
    return None


class AudioEngine:
    """Owns the dual capture streams + analyzers. Creator of the visualizer calls
    start() once; close() restores clean state. push() allows external callers
    (e.g. AK's TTS playback loop) to inject audio frames into the "speaker" channel."""

    def __init__(self):
        self.mic = AudioAnalyzer()
        self.spk = AudioAnalyzer()
        self.mic_stream = None
        self.spk_stream = None
        self._closed = False

    # -- callbacks (audio thread) -------------------------------------------
    def _mic_cb(self, indata, frames, time_info, status):
        self.mic.update(indata[:, 0])

    def _spk_cb(self, indata, frames, time_info, status):
        self.spk.update(indata[:, 0])

    # -- public API ----------------------------------------------------------
    def start(self):
        """Open microphone stream + (best-effort) speaker loopback stream."""
        try:
            self.mic_stream = sd.InputStream(
                samplerate=RATE, blocksize=CHUNK, channels=1,
                dtype="float32", callback=self._mic_cb,
            )
            self.mic_stream.start()
        except Exception as e:
            print(f"[audio] mic unavailable: {e}")

        dev = find_loopback_device()
        if dev is not None:
            try:
                self.spk_stream = sd.InputStream(
                    samplerate=RATE, blocksize=CHUNK, channels=1,
                    dtype="float32", device=dev, callback=self._spk_cb,
                    extra_settings=sd.WasapiSettings(loopback=True),
                )
                self.spk_stream.start()
                print("[audio] speaker loopback ON")
            except Exception as e:
                self.spk_stream = None
                print(f"[audio] loopback unavailable: {e}")
        else:
            print("[audio] loopback device not found - using mic only")

    def push(self, chunk: np.ndarray, channel="spk"):
        """External audio-injection hook: tts_wav chunk->speaker channel."""
        if self._closed:
            return
        (self.spk if channel == "spk" else self.mic).update(chunk)

    def snapshot(self):
        """Combined microphone + speaker audio model for one visual frame."""
        m = self.mic.snapshot()
        s = self.spk.snapshot()
        # volume = dominant of the two, weighted by which is "active"
        return {"mic": m, "spk": s,
                "volume": max(m["rms"], s["rms"] * 0.9)}

    def close(self):
        self._closed = True
        for st in (self.mic_stream, self.spk_stream):
            if st is not None:
                try:
                    st.stop()
                    st.close()
                except Exception:
                    pass