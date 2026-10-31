"""Speech-to-text for AK - everything local.

Why this exists: `speech_recognition.recognize_google()` sends audio to
Google's free endpoint using an API key that ships inside the library. That
key is now rejected (every request comes back as UnknownValueError), so AK
"heard" nothing and the user had to repeat himself 3-4 times. Silent failure,
no error shown - that was the real bug.

faster-whisper runs on the CPU with no network at all, handles Hinglish far
better than Vosk's small English model, and never phones home.
"""

import threading

import numpy as np

DEFAULT_MODEL = "base"          # good Hinglish, ~150 MB int8
TARGET_RATE = 16000

# ── Vocabulary bias ────────────────────────────────────────────────────────
# Whisper decodes greedily (beam_size used to be 1) with no idea what AK is
# for, so Indian names came back as phonetic garbage: "play arijit singh song"
# arrived as "play a rigid scene song" / "play a repeat since once" / "adjit
# sing songs" — and the play regex then matched nothing, so no song ever
# started.
#
# `hotwords` prepends these tokens to the decoder prompt, which is enough to
# pin the artist down even at 4 dB SNR (verified offline: baseline →
# "Play a Rigid Scene Song.", with this → "Play Arijit Singh song").
#
# Deliberately SPACE separated, not comma separated: the tokenizer turns a
# comma into a literal token and whisper then speaks it back
# ("play, Arijit, Singh, song.").
#
# Deliberately music-scoped. A general command wordlist ("volume", "screenshot",
# "open", "delete"…) would be far more dangerous here: a single injected word
# actually RUNS that command. These tokens are safe because the worst case is a
# YouTube search that should not have happened.
#
# NOT initial_prompt — that one is conditioned as *previous transcript text*,
# and on unclear audio whisper simply continues it: "kya time hai yahan"
# came back as "Play Arijit Singh song" and "screenshot lo" as "Playing songs
# on YouTube." Music would have started on its own. hotwords is a soft bias and
# never did that in testing.
DEFAULT_HOTWORDS = (
    "Arijit Singh song songs play YouTube music chalao bajao sunao gaana gaane "
    "Neha Kakkar Shreya Ghoshal Sonu Nigam Atif Aslam Armaan Malik "
    "Jubin Nautiyal Darshan Raval Pawan Singh Vishal Mishra "
    "Rahat Fateh Ali Khan Khesari Lal Shilpi Raj Vishal Dadlani "
    "Sunidhi Chauhan Shankar Mahadevan Kumar Sanu Alka Yagnik "
    "Udit Narayan Shaan Palak Muchhal B Praak Sachet Parampara"
)
DEFAULT_BEAM_SIZE = 1           # 1 = greedy; 5 costs ~10% and fixes spellings; speed over accuracy here


def resample(pcm: bytes, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    """int16 mono bytes -> float32 numpy array at dst_rate."""
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if src_rate and src_rate != dst_rate and len(audio) > 1:
        n_out = max(1, int(round(len(audio) * dst_rate / float(src_rate))))
        idx = np.linspace(0.0, len(audio) - 1.0, n_out)
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
    return audio


class LocalSTT:
    """faster-whisper on CPU, one model, one worker, guarded by a lock.

    cpu_threads caps how many cores it may use so AK's UI and speech output
    stay responsive; it is deliberately not set to every core.
    """

    def __init__(self, model_size=DEFAULT_MODEL, language="en-IN",
                 cpu_threads=2, compute_type="int8", vad=True,
                 hotwords=DEFAULT_HOTWORDS, beam_size=DEFAULT_BEAM_SIZE):
        self.model_size = model_size or DEFAULT_MODEL
        self.language = language or "en-IN"
        self.cpu_threads = max(1, int(cpu_threads or 2))
        self.compute_type = compute_type or "int8"
        self.vad = bool(vad)
        self.hotwords = (hotwords or "").strip() or None
        self.beam_size = max(1, int(beam_size or 1))
        self.name = f"faster-whisper:{self.model_size}/{self.compute_type}"
        self.ready = False
        self.error = ""
        self._model = None
        self._model_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    # ── model lifecycle ───────────────────────────────────────────
    def _ensure(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from faster_whisper import WhisperModel
                    self._model = WhisperModel(
                        self.model_size, device="cpu",
                        compute_type=self.compute_type,
                        cpu_threads=self.cpu_threads, num_workers=1)
                    self.ready = True
        return self._model

    def warmup(self):
        """Load the model off the hot path (first load = model download/read)."""
        def _run():
            try:
                self._ensure()
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
        threading.Thread(target=_run, daemon=True, name="ak-stt-warmup").start()

    # ── transcription ─────────────────────────────────────────────
    def transcribe(self, pcm: bytes, samplerate: int) -> str:
        if not pcm:
            return ""
        audio = resample(pcm, samplerate)
        lang = (self.language or "en").split("-")[0]
        model = self._ensure()
        with self._infer_lock:            # one inference at a time
            # vad_filter drops the leading/trailing quiet before the model
            # looks at it - same words out, ~20% less time, on this CPU.
            segments, _info = model.transcribe(
                audio, language=lang, beam_size=self.beam_size, best_of=1,
                vad_filter=self.vad,
                vad_parameters=dict(min_silence_duration_ms=300),
                condition_on_previous_text=False,
                without_timestamps=True,
                hotwords=self.hotwords)

            return " ".join(seg.text for seg in segments).strip()


class VoskSTT:
    """Fallback: Vosk small model. Fast, but poor with Hindi words."""

    def __init__(self, model=None, language="en-IN", cpu_threads=1):
        self._model = model
        self.language = language
        self.name = "vosk"
        self.ready = model is not None
        self.error = ""

    def transcribe(self, pcm: bytes, samplerate: int) -> str:
        if self._model is None or not pcm:
            return ""
        import json
        from vosk import KaldiRecognizer
        with self._infer_lock:
            rec = KaldiRecognizer(self._model, samplerate)
            rec.AcceptWaveform(pcm)
            return (json.loads(rec.Result()).get("text") or "").strip()

    _infer_lock = threading.Lock()
