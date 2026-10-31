"""Offline voiceprint lock — speaker verification with stdlib only.

Extracts a small set of log-mel band energies from a phrase and compares
them to an enrolled voiceprint via cosine similarity.  Good enough to
reject other speakers while letting the enrolled user through.

Fully offline: no ML libs, no network.  The voiceprint lives on the D drive.
"""

from __future__ import annotations

import json
import math
import os
import struct
from typing import List, Optional

NEW_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "config.json")
NEW_DP = "data_path"
DEFAULT_PATH = os.path.join(os.environ.get("D:", "D:"), "AK Assistant",
                            "voiceprint.json")


def _data_path() -> str:
    try:
        import json as j

        with open(NEW_DB_PATH, "r", encoding="utf-8") as fh:
            cfg = j.load(fh)
        p = cfg.get(NEW_DP)
        if p:
            return os.path.join(os.path.expandvars(os.path.expanduser(p)),
                                "voiceprint.json")
    except Exception:
        pass
    return DEFAULT_PATH


SAMPLERATE = 16000
FRAME = 4096
N_BANDS = 24
MEL_MIN = 50.0
MEL_MAX = 4000.0


def _mel(f: float) -> float:
    return 2595.0 * math.log10(1.0 + f / 700.0)


def _band_edges() -> List[List[int]]:
    mel0, mel1 = _mel(MEL_MIN), _mel(MEL_MAX)
    edges = []
    for i in range(N_BANDS + 1):
        m = mel0 + (mel1 - mel0) * i / N_BANDS
        edges.append(int(round(700.0 * (10.0 ** (m / 2595.0) - 1.0))))
    out = []
    for i in range(N_BANDS):
        lo = math.ceil(edges[i] * FRAME / SAMPLERATE)
        hi = math.floor(edges[i + 1] * FRAME / SAMPLERATE)
        out.append([max(lo, 1), min(hi, FRAME // 2)])
    return out


def _fft_mag(x: List[float]) -> List[float]:
    """Radix-2 iterative FFT on a power-of-two input; returns magnitudes."""
    n = len(x)
    a = [complex(v, 0.0) for v in x]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        ang = -2.0 * math.pi / length
        wr, wi = math.cos(ang), math.sin(ang)
        half = length >> 1
        for i in range(0, n - length + 1, length):
            tw = complex(1.0, 0.0)
            for k in range(half):
                u = a[i + k]
                v = a[i + k + half] * tw
                a[i + k] = u + v
                a[i + k + half] = u - v
                tw *= complex(wr, wi)
        length <<= 1
    return [abs(z) for z in a[:n // 2 + 1]]


def _window() -> List[float]:
    """42-term Blackman-Harris-ish approximation via Hann-cubed (cheap)."""
    n = FRAME
    x = math.tau / (n - 1)
    return [0.5 - 0.5 * math.cos(x * i) for i in range(n)]


def _features(audio: bytes) -> Optional[List[float]]:
    """Multi-frame spectral voiceprint: average log-mel band energies over
    several 256 ms windows around the loudest part of the utterance, then
    center (match spectral SHAPE, not overall tilt) and L2-normalize.

    Averaging several frames makes matching far more stable than a single
    window — same speaker stays high even with noise/mic variance."""
    try:
        samples = struct.unpack("<%dh" % (len(audio) // 2), audio)
    except Exception:
        return None
    n = len(samples)
    if n < FRAME:
        return None
    step = FRAME // 2
    # Anchor = highest-energy 4096-sample window.
    best, best_e = 0, -1.0
    for off in range(0, n - FRAME + 1, step):
        e = 0.0
        for i in range(off, off + FRAME):
            e += samples[i] * samples[i]
        if e > best_e:
            best_e = e
            best = off
    if best_e <= 0:
        return None
    positions = []
    for d in (0, 1, -1, 2, -2, 3, -3):
        p = best + d * step
        if 0 <= p <= n - FRAME and p not in positions:
            positions.append(p)
    win = _window()
    edges = _band_edges()
    acc = [0.0] * N_BANDS
    cnt = 0
    for p in positions[:8]:
        x = [samples[p + i] * win[i] for i in range(FRAME)]
        mag = _fft_mag(x)
        for b, (lo, hi) in enumerate(edges):
            pw = 0.0
            for k in range(lo, hi):
                pw += mag[k] * mag[k]
            acc[b] += math.log10(pw + 1e-8)
        cnt += 1
    if not cnt:
        return None
    vec = [a / cnt for a in acc]
    mean = sum(vec) / len(vec)
    vec = [v - mean for v in vec]
    ln = math.sqrt(sum(v * v for v in vec))
    if ln <= 0:
        return None
    return [v / ln for v in vec]


class Voiceprint:
    """Enroll a speaker's voice and verify later phrases against it."""

    def __init__(self, path: str = ""):
        self.path = path or _data_path()
        self._vectors: List[List[float]] = []
        self.threshold = 0.85

    def add_sample(self, audio: bytes) -> Optional[List[float]]:
        vec = _features(audio)
        if vec:
            self._vectors.append(vec)
        return vec

    def auto_threshold(self) -> float:
        """Set the threshold from the user's OWN voice variability: a value
        just below their worst self-similarity, clamped to a sane range so it
        can't accept everyone or lock the owner out."""
        if len(self._vectors) < 2:
            self.threshold = 0.80
            return self.threshold
        worst = 1.0
        for i in range(len(self._vectors)):
            for j in range(i + 1, len(self._vectors)):
                s = sum(a * b for a, b in zip(self._vectors[i], self._vectors[j]))
                worst = min(worst, s)
        self.threshold = min(max(worst * 0.98, 0.60), 0.88)
        return self.threshold

    def save(self, path: str = "") -> bool:
        target = path or self.path
        if not self._vectors:
            return False
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump({"v": self._vectors, "n": len(self._vectors),
                           "t": self.threshold, "vpv": 2}, fh)
            return True
        except Exception:
            return False

    def load(self, path: str = "") -> bool:
        """Load a voiceprint.  Returns False for old/mismatched feature
        versions (caller then asks the user to re-enroll)."""
        target = path or self.path
        try:
            with open(target, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("vpv") != 2:
                return False
            self._vectors = [list(map(float, v)) for v in data.get("v", [])]
            if "t" in data:
                self.threshold = float(data["t"])
            elif len(self._vectors) >= 2:
                self.auto_threshold()
            return bool(self._vectors)
        except Exception:
            return False

    @property
    def enrolled(self) -> bool:
        return bool(self._vectors)

    @property
    def samples(self) -> int:
        return len(self._vectors)

    def _score(self, vec: List[float]) -> float:
        best = -1.0
        for ref in self._vectors:
            s = sum(a * b for a, b in zip(vec, ref))
            best = max(best, s)
        return best

    def matches(self, audio: bytes, threshold: Optional[float] = None) -> bool:
        if not self._vectors:
            return True
        vec = _features(audio)
        if vec is None:
            return False
        thr = threshold if threshold is not None else self.threshold
        return self._score(vec) >= thr

    def score(self, audio: bytes) -> float:
        vec = _features(audio)
        if vec is None:
            return -1.0
        return self._score(vec)