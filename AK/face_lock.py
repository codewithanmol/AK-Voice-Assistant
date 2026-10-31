"""Face lock — only your registered face can use AK.

Mirrors the voice-lock design: you enroll 2 face samples with the camera; from
then on AK blinks the camera at startup, recognises YOUR face and only then
lets you use it. The camera window opens while it looks at you and closes the
moment it decides (match = unlock, no-match = keep waiting / fail politely).

Fully offline: OpenCV Haar cascade for detection + LBPH for recognition, no
AI model download, no internet, no dlib.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import threading
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

_SAY_LOCK = threading.Lock()


def _camera_available(index: int = 0) -> bool:
    """Check without leaving a stuck handle behind."""
    try:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            return False
        ok, _ = cap.read()
        cap.release()
        return ok
    except Exception:
        return False


class FaceLock:
    """OpenCV-based face enrollment + one-on-one recognition."""

    def __init__(self, data_dir: str, threshold: float = 90.0):
        self.data_dir = data_dir
        self.threshold = float(threshold)
        self.users_path = os.path.join(data_dir, "users.json")
        self.model_path = os.path.join(data_dir, "face_model.yml")
        self._labels: dict  # user label -> name
        self._recognizer = None
        self._cascade = None
        os.makedirs(self.data_dir, exist_ok=True)
        self._load()

    # ── cascade (downloaded once into AK/face/) ──
    @staticmethod
    def _cascade_path() -> Optional[str]:
        here = os.path.dirname(os.path.abspath(__file__))
        cands = [
            os.path.join(here, "face", "haarcascade_frontalface_default.xml"),
            os.path.join(cv2.data.haarcascades,
                         "haarcascade_frontalface_default.xml")
            if hasattr(cv2.data, "haarcascades") else "",
        ]
        for c in cands:
            if c and os.path.isfile(c):
                return c
        return None

    def _load(self):
        self._labels = {}
        if os.path.isfile(self.users_path):
            try:
                with open(self.users_path, encoding="utf-8") as fh:
                    self._labels = {int(k): v for k, v in
                                    (json.load(fh) or {}).items()}
            except Exception:
                self._labels = {}
        if os.path.isfile(self.model_path):
            try:
                rec = cv2.face.LBPHFaceRecognizer_create()
                rec.read(self.model_path)
                self._recognizer = rec
            except Exception:
                self._recognizer = None

    # ── enrollment ───────────────────────────────────────────────────────────
    def enroll(self, name: str, samples: int = 16, cam_index: int = 0
               ) -> Tuple[str, bool]:
        """Open the camera, capture the user's face, train & save the model."""
        name = (name or "user").strip().lower()
        if not self._cascade_path():
            return ("Face detection model missing. Re-download "
                    "haarcascade_frontalface_default.xml into AK/face/ "
                    "and try again.", False)
        cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return ("Camera nahi khul payi — check karo ki webcam connect hai "
                    "aur koi aur app use toh nahi kar raha.", False)
        cascade = cv2.CascadeClassifier(self._cascade_path())
        if cascade.empty():
            cap.release()
            return "Face detection model could not load.", False

        gray_patches = []
        collected = 0
        shown_guide = False
        deadline = time.time() + 25
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
        cv2.namedWindow("AK — Face Enroll", cv2.WINDOW_NORMAL)
        cv2.moveWindow("AK — Face Enroll", 120, 90)
        cv2.resizeWindow("AK — Face Enroll", 460, 360)
        try:
            while time.time() < deadline:
                ok, frame = cap.read()
                if not ok:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5,
                    minSize=(92, 92))
                big = faces[0] if len(faces) else None
                for (x, y, w, h) in faces[:1]:
                    big = (x, y, w, h)
                    cv2.rectangle(frame, (x, y), (x + w, y + h),
                                  (80, 255, 90), 2)
                    cv2.putText(frame, f"sample {collected}/{samples}",
                                (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (80, 255, 90), 2)
                if not shown_guide:
                    cv2.putText(frame, "Camera ke saamne dekho...",
                                (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                                (255, 255, 255), 2)
                    shown_guide = True
                cv2.imshow("AK — Face Enroll", frame)
                cv2.waitKey(1)
                if big is None:
                    continue
                x, y, w, h = big
                x, y = max(x - 6, 0), max(y - 6, 0)
                if x + w + 12 > gray.shape[1] or y + h + 12 > gray.shape[0]:
                    continue
                patch = gray[y:y + h + 12, x:x + w + 12]
                patch = cv2.resize(patch, (200, 200)).astype(np.uint8)
                if patch.std() < 12:      # too dark / washed out, skip
                    continue
                gray_patches.append(patch)
                collected += 1
                if collected >= samples:
                    break
                time.sleep(0.30)
        finally:
            cap.release()
            cv2.destroyAllWindows()

        if collected < 4:
            return ("Enough clear samples nahi mile — 4+ chaahiye. Camera ke "
                    "saamne roshni mein baithein aur 'register face' dobara "
                    "boley.", False)

        label = max(self._labels.keys(), default=-1) + 1
        if label < 0:
            label = 0
        self._labels[label] = name
        ylabels = np.array([label] * len(gray_patches), np.int32)
        rec = cv2.face.LBPHFaceRecognizer_create()
        rec.train(gray_patches, ylabels)
        rec.write(self.model_path)
        with open(self.users_path, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in self._labels.items()}, fh,
                      indent=2, ensure_ascii=False)
        self._recognizer = rec
        return (f"Face registered: {name} — {collected} clear samples se "
                f"training ho gayi. Ab camera sirf aapko pehchaanta hai.", True)

    # ── authenticate ─────────────────────────────────────────────────────────
    def authenticate(self, cam_index: int = 0, retries: int = 3
                     ) -> Tuple[str, bool]:
        """Camera kholega, aapka chehra dhoondega, match → True + name,
        warna False. Window match/fail ke turant band ho jaati hai."""
        if self._recognizer is None or not self._labels:
            return ("No face enrolled yet. Say 'register my face' to teach me "
                    "your face first.", False)
        if not self._camera_available_now:
            return ("Camera available nahi hai — face unlock me use karna "
                    "kaam nahi karega jab tak webcam nahi milta.", False)
        cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return ("Camera nahi khul payi. Check karo webcam hai ya koi app "
                    "use kar raha hai.", False)
        cascade = cv2.CascadeClassifier(self._cascade_path())
        rec = self._recognizer
        name_rev = {v: k for k, v in self._labels.items()}
        decided = False
        verdict_name = None
        cv2.namedWindow("AK — Face Lock", cv2.WINDOW_NORMAL)
        cv2.moveWindow("AK — Face Lock", 180, 110)
        cv2.resizeWindow("AK — Face Lock", 430, 330)
        try:
            for _ in range(retries):
                start = time.time()
                while time.time() - start < 6:
                    ok, frame = cap.read()
                    if not ok:
                        continue
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    faces = cascade.detectMultiScale(
                        gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
                    match_name = None
                    conf = None
                    for (x, y, w, h) in faces[:1]:
                        cx, cy = max(x - 8, 0), max(y - 8, 0)
                        if cx + w + 16 > gray.shape[1] or cy + h + 16 > gray.shape[0]:
                            break
                        patch = cv2.resize(
                            gray[cy:cy + h + 16, cx:cx + w + 16],
                            (200, 200)).astype(np.uint8)
                        label, conf = rec.predict(patch)
                        if conf <= self.threshold:
                            match_name = self._labels.get(int(label))
                        cv2.rectangle(frame, (x, y), (x + w, y + h),
                                      (0, 200, 255), 2)
                    if match_name:
                        verdict_name = match_name
                        decided = True
                        cv2.putText(frame, f"Welcome, {match_name}!",
                                    (18, 40), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.9, (80, 255, 120), 2)
                    else:
                        cv2.putText(frame, "Pehlaanta nahi... apna chehra dikhao",
                                    (18, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                    (255, 255, 255), 2)
                    cv2.imshow("AK — Face Lock", frame)
                    cv2.waitKey(1)
                    if decided:
                        time.sleep(0.6)     # let them see the welcome
                        break
                if decided:
                    break
        finally:
            cap.release()
            cv2.destroyAllWindows()

        if decided:
            return (verdict_name, True)
        return ("Aapka chehra register nahi hai. Sirf registered log hi AK use "
                "kar sakte hain. Say 'register my face' to add someone.", False)

    # ── watch mode ──────────────────────────────────────────────────────────
    def watch(self, duration: float = 25.0, idle_quit: float = 6.0,
              cam_index: int = 0,
              on_face: Optional[Callable[[str], None]] = None) -> str:
        """Watch mode: jab bhi koi naya chehra camera ke saamne aaye, use
        announce karo (on_face callback), phir summary do ki kis kis ko dekha."""
        if not self._cascade_path():
            return ("Face detection model missing. Re-download "
                    "haarcascade_frontalface_default.xml into AK/face/ "
                    "and try again.")
        if not self._camera_available_now:
            return "Camera available nahi hai — webcam check karo."
        cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return ("Camera nahi khul payi. Check karo webcam connect hai "
                    "aur koi aur app use toh nahi kar raha.")
        cascade = cv2.CascadeClassifier(self._cascade_path())
        if cascade.empty():
            cap.release()
            return "Face detection model load nahi hua."
        cv2.namedWindow("AK — Camera Watch", cv2.WINDOW_NORMAL)
        cv2.moveWindow("AK — Camera Watch", 180, 110)
        cv2.resizeWindow("AK — Camera Watch", 430, 330)
        labels = self._labels
        rec = self._recognizer
        seen: dict = {}        # text -> [first_seen, last_seen]
        order: List[str] = []  # first-appearance order of unique people
        deadline = time.time() + duration
        last_any = time.time()
        try:
            while time.time() < deadline:
                ok, frame = cap.read()
                if not ok:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
                if faces:
                    last_any = time.time()
                present = set()
                for (x, y, w, h) in faces:
                    cx, cy = max(x - 8, 0), max(y - 8, 0)
                    if cx + w + 16 > gray.shape[1] or cy + h + 16 > gray.shape[0]:
                        continue
                    patch = cv2.resize(
                        gray[cy:cy + h + 16, cx:cx + w + 16],
                        (200, 200)).astype(np.uint8)
                    text = "Unknown person"
                    if rec is not None and labels:
                        try:
                            label, conf = rec.predict(patch)
                            if conf <= self.threshold:
                                text = labels.get(int(label), "Unknown person")
                        except Exception:
                            text = "Unknown person"
                    known = text != "Unknown person"
                    cv2.rectangle(frame, (x, y), (x + w, y + h),
                                  (80, 255, 90) if known else (0, 200, 255), 2)
                    cv2.putText(frame, text, (x, y - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                (80, 255, 90) if known else (0, 200, 255), 2)
                    present.add(text)
                ts = time.time()
                for text in present:
                    if text not in seen:
                        seen[text] = [ts, ts]
                        order.append(text)
                        if on_face:
                            on_face(text)
                    else:
                        seen[text][1] = ts
                if not present and time.time() - last_any > idle_quit:
                    break
                cv2.putText(frame, "Watch — camera ke saamne dekho",
                            (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (255, 255, 255), 2)
                cv2.imshow("AK — Camera Watch", frame)
                cv2.waitKey(1)
        finally:
            cap.release()
            cv2.destroyAllWindows()

        if not order:
            return "Camera ke saamne is beech koi nahi aaya."
        parts = []
        for t in order:
            first, last = seen[t]
            n = 1 + int(round((last - first) / 2.0))
            parts.append(t if n == 1 else f"{t} ({n} baar)")
        return "Camera ke saamne aaye: " + ", ".join(parts) + "."

    # ── management ───────────────────────────────────────────────────────────
    def list_users(self) -> str:
        if not self._labels:
            return "Koi face registered nahi hai. Say 'register my face'."
        names = ", ".join(sorted(v for v in self._labels.values()))
        return f"{len(self._labels)} face registered: {names}."

    def remove(self, name: str) -> Tuple[str, bool]:
        name = (name or "").strip().lower()
        for k, v in list(self._labels.items()):
            if v == name:
                del self._labels[k]
                break
        else:
            return (f"'{name}' registered face nahi mila. "
                    f"{self.list_users()}", False)
        if self._labels:
            with open(self.users_path, "w", encoding="utf-8") as fh:
                json.dump({str(k): v for k, v in self._labels.items()}, fh,
                          indent=2, ensure_ascii=False)
            if self._recognizer is not None:
                xs = self._collect_all()   # re-train without removed user
                if xs:
                    rec = cv2.face.LBPHFaceRecognizer_create()
                    rec.train(xs[0], xs[1])
                    rec.write(self.model_path)
                    self._recognizer = rec
        else:
            if os.path.isfile(self.model_path):
                os.remove(self.model_path)
            if os.path.isfile(self.users_path):
                os.remove(self.users_path)
            self._recognizer = None
        return (f"'{name}' ko faces list se hata diya.", True)

    def _collect_all(self):
        # can't re-train from the stored model; only re-enroll next time.
        return None

    @property
    def _camera_available_now(self) -> bool:
        return _camera_available()

    @property
    def enrolled(self) -> bool:
        return bool(self._labels)
