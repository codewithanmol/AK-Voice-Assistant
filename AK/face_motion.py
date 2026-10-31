# AK face motion
# The same photo, but alive in a different way.
#
# The head is the real face - every pixel of the picture is a particle and
# the landmarks are drawn as light on top of it. Around that head a body is
# grown: shoulders, torso, arms and legs, all made of the same light.
#
# The world is longer than the screen. The figure walks through a row of
# rooms - the camera follows him, so every room looks like a new screen:
#   lounge  - he sits down on the sofa and rests
#   play    - he chases the ball and kicks it
#   desk    - he sits at the PC and types
#   window  - he stops at the window and looks out
# and then walks on to the next room.
import math
import os
import random

import numpy as np
import pygame

try:
    from .vis_core import Emitter, _hsv
except ImportError:          # run as a plain script
    from vis_core import Emitter, _hsv

try:
    from .face_portrait import Portrait, portrait_path
except ImportError:          # run as a plain script
    from face_portrait import Portrait, portrait_path


# ── the body, in body units (feet at 0, top of the head at 1.0) ───────────
HEAD_H = 0.135
CHIN_Y = 1.0 - HEAD_H          # 0.865
NECK_Y = 0.838
SHOULDER_Y = 0.812
HIP_Y = 0.500
TORSO_LEN = SHOULDER_Y - HIP_Y
SHOULDER_HW = 0.124
HIP_HW = 0.086
UPPER_ARM = 0.160
LOWER_ARM = 0.150
THIGH = 0.245
SHIN = 0.225
FOOT_LEN = 0.078

DESK_H = 0.52                  # the desk is a counter, so the arms reach it
SEAT_DROP = 0.115              # the sofa is lower than the floor

ZONE_W = 1250.0                # one room is this wide in world pixels
ZONE_KINDS = ("lounge", "play", "desk", "window")


class MotionStage:
    """The photo on a walking body, with a life of its own.

    The head keeps the picture (particles + the landmarks as light), the body
    is grown out of light around it. The world is a row of rooms and the
    figure walks through them, doing what each room is for.
    """

    def __init__(self, w, h, n=5200):
        self.w, self.h = w, h
        # a smaller head - the rest of the light belongs to the body
        self.portrait = Portrait.load(n=3400)
        self.ok = self.portrait is not None
        self.t = 0.0
        self.level = 0.0
        self.peak = 0.0
        self.hue = 40.0
        self.em = Emitter(w, h)
        self.em.fade.fill((150, 150, 150, 255))
        self.pt = pygame.Surface((max(2, w), max(2, h)), 0, 24)
        # one surface for every line of light - rooms, body and face together.
        # three full screen blits a frame were the whole budget
        self.vsurf = pygame.Surface((max(2, w), max(2, h)), pygame.SRCALPHA)
        self.bg = None
        self.bg_key = -1

        # the walk
        self.phase = 0.6
        self.wx = 0.0              # the world position
        self.cam = -w * 0.42       # the camera (the left edge of the screen)
        self.x = w * 0.42          # the screen position
        self.dir = 1.0             # +1 walks right, -1 walks left
        self.turn = 0.0            # 0..1 while the figure turns around
        self.turning = False
        self.speed = 0.22          # body units per second
        self.hop = 0.0             # the jump, 0..1
        self.hop_h = 0.0
        self._plant = 0
        self.dust = []
        self._mouse = (0.0, 0.0)
        self._px = 0.0
        self._py = 0.0
        self._tilt = 0.0

        # the life
        self.zone = 0              # the room he is heading to
        self.activity = "walk"     # walk | lounge | play | desk | window
        self.act_t = 0.0
        self.act_len = 3.0
        self.target_wx = None
        self.ball = None
        self.kicks = 0
        self.type_t = 0.0
        self.screen_glow = 0.0
        self.look = 0.0            # the head turning to the side

        self.head_local = np.zeros((0, 2))
        self.head_col = np.zeros((0, 3))
        self.head_warm = np.zeros(0)
        self.parts = []
        self.tf = {}
        self.head_tf = (0.0, CHIN_Y, 0.0)
        if self.ok:
            self._build(n)

    # ── building the figure ────────────────────────────────────────
    def _build(self, n):
        p = self.portrait
        a = p.anchors
        rng = np.random.default_rng(23)

        chin = np.asarray(a["chin"], np.float64)
        face_h = float(a["face_h"])
        face_cx = float(a["face_cx"])
        shirt = np.clip(np.asarray(a["shirt"], np.float64), 0.0, 1.0)
        skin = np.clip(np.asarray(a["skin"], np.float64), 0.0, 1.0)

        # ── the head: the real picture ─────────────────────────────
        per = HEAD_H / max(1e-4, face_h * 1.34)
        keep = p.pts[:, 1] <= chin[1] + 0.04 * face_h     # drop the shoulders
        hp = p.pts[keep]
        # the head hangs above the chin - the chin is the anchor
        lx = (hp[:, 0] - face_cx) * per
        ly = (chin[1] - hp[:, 1]) * per
        self.head_local = np.stack([lx, ly], axis=1)
        self.head_col = p.rgb[keep]
        self.head_warm = p.detail[keep]
        self._head_per = per
        self._skin = skin
        self._shirt = shirt
        self._trouser = np.clip(shirt * 0.55 + np.array([0.02, 0.02, 0.05]),
                                0.0, 1.0)

        # ── the body ──────────────────────────────────────────────
        trouser = self._trouser
        shoe = np.clip(trouser * 0.45, 0.0, 1.0)

        def capsule(count, length, radius, taper=0.30):
            t = rng.random(count) * length
            ang = rng.random(count) * 6.2832
            rad = np.sqrt(rng.random(count)) * radius * \
                (1.0 - taper * (t / max(1e-4, length)))
            x = np.cos(ang) * rad
            y = -t + np.sin(ang) * rad
            return np.stack([x, y], axis=1)

        def torso_pts(count):
            u = rng.random(count) * 2.0 - 1.0
            v = rng.random(count) * 2.0 - 1.0
            y = (v * 0.5 + 0.5) * TORSO_LEN
            f = y / TORSO_LEN
            hw = HIP_HW + (SHOULDER_HW - HIP_HW) * f
            x = u * hw * np.sqrt(np.maximum(0.0, 1.0 - v * v))
            return np.stack([x, y], axis=1)

        def foot_pts(count):
            x = rng.random(count) * FOOT_LEN
            y = (rng.random(count) - 0.35) * 0.040
            return np.stack([x, y], axis=1)

        def neck_pts(count):
            t = rng.random(count) * (CHIN_Y - NECK_Y)
            ang = rng.random(count) * 6.2832
            rad = np.sqrt(rng.random(count)) * 0.030
            return np.stack([np.cos(ang) * rad, t + np.sin(ang) * rad], axis=1)

        parts = []

        def add(name, local, color, kind):
            parts.append({"name": name, "local": local, "col": color,
                          "kind": kind})

        rest = max(600, n - len(self.head_local))
        share = rest / 9.0
        add("torso", torso_pts(int(share * 2.0)),
            np.tile(shirt, (int(share * 2.0), 1)), "torso")
        add("neck", neck_pts(int(share * 0.6)),
            np.tile(skin, (int(share * 0.6), 1)), "neck")
        for side in ("far", "near"):
            add("ua_" + side, capsule(int(share * 0.9), UPPER_ARM, 0.030),
                np.tile(shirt, (int(share * 0.9), 1)), "upper_arm")
            add("la_" + side, capsule(int(share * 0.8), LOWER_ARM, 0.024),
                np.tile(skin, (int(share * 0.8), 1)), "lower_arm")
            add("thigh_" + side, capsule(int(share * 1.1), THIGH, 0.040),
                np.tile(trouser, (int(share * 1.1), 1)), "thigh")
            add("shin_" + side, capsule(int(share * 1.0), SHIN, 0.030),
                np.tile(trouser, (int(share * 1.0), 1)), "shin")
            add("foot_" + side, foot_pts(int(share * 0.5)),
                np.tile(shoe, (int(share * 0.5), 1)), "foot")
        self.parts = parts

        # the ball - a small cloud of light
        rngb = np.random.default_rng(41)
        m = 90
        d = rngb.normal(size=(m, 3))
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        self.ball_pts = d * (rngb.random(m) ** 0.6 * 0.030 +
                             0.006)[:, None]

    # ── the walk cycle ─────────────────────────────────────────────
    def _pose(self):
        p = self.phase
        sitting = self.activity == "lounge" and self.target_wx is None
        working = self.activity == "desk" and self.target_wx is None

        bob = -0.014 * abs(math.sin(p))
        sway = 0.013 * math.sin(p)
        twist = 0.08 * math.sin(p)
        hip_y = HIP_Y + bob
        sh_y = SHOULDER_Y + bob

        if sitting:
            # he sits down on the sofa, the legs fold forward
            bob = 0.0
            sway = 0.004 * math.sin(self.t * 0.8)
            twist = 0.06
            hip_y = HIP_Y - SEAT_DROP
            sh_y = SHOULDER_Y - SEAT_DROP
        if working:
            # it stands at the desk and types
            bob = 0.0
            sway = 0.004 * math.sin(self.type_t * 3.1)
            twist = 0.10
            hip_y = HIP_Y
            sh_y = SHOULDER_Y

        tf = {}

        # the legs - the far one is a little behind and dimmer
        for name, ph, near in (("near", p, True), ("far", p + math.pi, False)):
            if sitting:
                th = 1.30
                knee = 1.52
            else:
                th = 0.30 * math.sin(ph)
                knee = 0.88 * max(0.0, -math.cos(ph)) ** 1.15
            hx = sway + (0.020 if near else -0.020)
            hip = (hx, hip_y)
            kneep = (hip[0] + THIGH * math.sin(th),
                     hip[1] - THIGH * math.cos(th))
            psi = th - knee
            ank = (kneep[0] + SHIN * math.sin(psi),
                   kneep[1] - SHIN * math.cos(psi))
            tf["thigh_" + name] = (hip[0], hip[1], th)
            tf["shin_" + name] = (kneep[0], kneep[1], psi)
            tf["foot_" + name] = (ank[0], ank[1], 0.35 * psi)

        # the arms - they swing against the legs
        for name, ph, near in (("near", p + math.pi, True),
                               ("far", p, False)):
            ang = 0.44 * math.sin(ph)
            elbow = 0.30 + 0.14 * math.sin(ph + 1.0)
            if working:
                # both hands on the keyboard, typing
                ty = math.sin(self.type_t * 9.0 + (0.0 if near else 0.7))
                ang = 0.62 + (0.10 if near else 0.16) * ty
                elbow = 0.78
            elif sitting:
                # the hands rest on the knees
                ang = 0.46 + 0.03 * math.sin(self.t * 0.9 + ph)
                elbow = 0.62
            sx = sway * 0.45 + (0.032 if near else -0.032)
            sh = (sx, sh_y)
            el = (sh[0] + UPPER_ARM * math.sin(ang),
                  sh[1] - UPPER_ARM * math.cos(ang))
            hand = (el[0] + LOWER_ARM * math.sin(ang + elbow),
                    el[1] - LOWER_ARM * math.cos(ang + elbow))
            tf["ua_" + name] = (sh[0], sh[1], ang)
            tf["la_" + name] = (el[0], el[1], ang + elbow)

        tf["torso"] = (sway, hip_y, twist)
        tf["neck"] = (sway * 0.45, NECK_Y + bob - (SEAT_DROP if sitting else 0.0),
                      twist * 0.4)
        self.tf = tf
        head_tilt = -0.05 * math.sin(p) + self._tilt
        if working:
            head_tilt = 0.16 + self._tilt * 0.5     # it looks at the screen
        elif sitting:
            head_tilt = 0.05 + 0.02 * math.sin(self.t * 0.7)
        elif self.activity == "window":
            head_tilt = -0.14 + self._tilt * 0.5    # it looks out
        self.head_tf = (sway * 0.35, CHIN_Y + bob * 1.15 -
                        (SEAT_DROP if sitting else 0.0), head_tilt)

    # ── the frame ──────────────────────────────────────────────────
    def update(self, dt, level, peak, bands, centroid, hue, state, speaking,
               phrase=""):
        self.t += dt
        self.level = level
        self.peak = peak
        self.hue = hue

        # the head looks at the cursor, a little
        try:
            mx = (self._mouse[0] - self.w * 0.5) / max(1.0, self.w * 0.5)
            my = (self._mouse[1] - self.h * 0.5) / max(1.0, self.h * 0.5)
        except Exception:
            mx = my = 0.0
        self._px += (mx - self._px) * 0.05
        self._py += (my - self._py) * 0.05
        self._tilt = -self._px * 0.10

        scale = self._scale()
        self._schedule(dt, scale)
        self._move(dt, scale)
        self._ball_step(dt, scale)

        # the camera follows the figure
        want_cam = self.wx - self.w * 0.42
        self.cam += (want_cam - self.cam) * min(1.0, dt * 3.2)
        self.x = self.wx - self.cam

        # the walk
        moving = not (self.activity in ("desk", "lounge")
                      and self.target_wx is None)
        if moving:
            self.phase += dt * 5.2 * (1.0 + 0.25 * min(1.0, level))
        else:
            self.type_t += dt

        # the jump
        if self.hop > 0.0:
            self.hop = max(0.0, self.hop - dt * 2.4)
            self.hop_h = 4.0 * self.hop * (1.0 - self.hop) * 0.13

        # a foot lands - the dust comes up
        step = int(self.phase / math.pi)
        if step != self._plant:
            self._plant = step
            self._puff()

        self._pose()

        # the dust lives and dies
        alive = []
        for d in self.dust:
            d[2] += dt
            d[0] += d[4] * dt
            d[1] += d[5] * dt
            d[5] -= dt * 0.6
            if d[2] < d[3]:
                alive.append(d)
        self.dust = alive

        self.screen_glow = max(0.0, self.screen_glow - dt * 2.0)

    # ── the life: what is it doing now ────────────────────────────
    def _zone_kind(self, i):
        return ZONE_KINDS[int(i) % len(ZONE_KINDS)]

    def _zone_origin(self, i):
        return i * ZONE_W

    def _zone_spot(self, i):
        """Where the figure stands in that room (world x)."""
        o = self._zone_origin(i)
        kind = self._zone_kind(i)
        scale = self._scale()
        if kind == "desk":
            return o + ZONE_W * 0.60 - 0.10 * scale
        if kind == "lounge":
            return o + ZONE_W * 0.44 - 0.20 * scale
        if kind == "window":
            return o + ZONE_W * 0.46 - 0.12 * scale
        return o + ZONE_W * 0.50

    def _schedule(self, dt, scale):
        self.act_t += dt
        if self.activity == "walk":
            if self.target_wx is not None:
                if abs(self.target_wx - self.wx) < 14.0:
                    self._begin_activity(self._zone_kind(self.zone))
            elif self.act_t > self.act_len:
                # on to the next room
                self.zone += 1
                self.target_wx = self._zone_spot(self.zone)
                self.act_t = 0.0
        elif self.activity == "play":
            if self.ball is None or self.act_t > self.act_len:
                self.activity = "walk"
                self.act_t = 0.0
                self.act_len = 2.5 + random.random() * 2.5
                self.target_wx = None
                self.ball = None
            else:
                # chase the ball
                self.target_wx = self.ball["wx"]
        else:
            # lounge / desk / window - he stays for a while
            if self.act_t > self.act_len:
                self.activity = "walk"
                self.act_t = 0.0
                self.act_len = 2.5 + random.random() * 2.5
                self.target_wx = None

    def _begin_activity(self, kind):
        self.activity = kind
        self.act_t = 0.0
        self.target_wx = None
        if kind == "play":
            self._spawn_ball(self._scale())
            self.act_len = 9.0 + random.random() * 6.0
        elif kind == "desk":
            self.act_len = 7.0 + random.random() * 5.0
            self.type_t = 0.0
        elif kind == "lounge":
            self.act_len = 6.0 + random.random() * 4.0
        else:
            self.act_len = 5.0 + random.random() * 4.0

    def _move(self, dt, scale):
        if self.turning:
            self.turn += dt * 2.0
            if self.turn >= 1.0:
                self.turn = 0.0
                self.turning = False
                self.dir = -self.dir
            return
        if self.activity in ("desk", "lounge") and self.target_wx is None:
            return                      # it stays where it is
        if self.target_wx is not None:
            want = 1.0 if self.target_wx > self.wx else -1.0
            if abs(self.target_wx - self.wx) < 12.0:
                return                      # it arrived
            if want != self.dir:
                self.turning = True
                return
            spd = self.speed
            if self.activity == "play":
                spd *= 1.9                # it runs after the ball
            self.wx += self.dir * spd * scale * dt
            return
        # a free walk: it turns around at the edges
        self.wx += self.dir * self.speed * scale * dt
        margin = 0.18 * scale
        if self.x > self.w + margin or self.x < -margin:
            self.turning = True

    # ── the ball ──────────────────────────────────────────────────
    def _spawn_ball(self, scale):
        """A ball of light is dropped in front of it."""
        side = random.choice((-1.0, 1.0))
        o = self._zone_origin(self.zone)
        self.ball = {"wx": self.wx + side * scale * 0.30,
                     "y": 0.32,
                     "vx": side * scale * 0.22,
                     "vy": 0.0,
                     "r": 0.030}
        self.kicks = 0

    def _ball_step(self, dt, scale):
        b = self.ball
        if b is None:
            return
        b["vy"] -= 2.4 * dt
        b["wx"] += b["vx"] * dt
        b["y"] += b["vy"] * dt
        b["vx"] *= (1.0 - 0.30 * dt)           # the air drags a little too
        if b["y"] < b["r"]:
            b["y"] = b["r"]
            b["vy"] = abs(b["vy"]) * 0.52
            if b["vy"] < 0.08:
                b["vy"] = 0.0
            b["vx"] *= (1.0 - 1.4 * dt)          # it rolls and slows down
        # the ball bounces off the walls of the room
        o = self._zone_origin(self.zone)
        lo = o + 0.10 * scale
        hi = o + ZONE_W - 0.10 * scale
        if b["wx"] < lo:
            b["wx"] = lo
            b["vx"] = abs(b["vx"]) * 0.45
        elif b["wx"] > hi:
            b["wx"] = hi
            b["vx"] = -abs(b["vx"]) * 0.45

        # the kick: the foot meets the ball
        f = self.tf.get("foot_near")
        if f is None:
            return
        flip = self._flip()
        fx = self.wx + f[0] * scale * flip
        fy = self._ground() - f[1] * scale
        d = math.hypot(fx - b["wx"], fy - (self._ground() - b["r"] * scale))
        if d < scale * 0.060 and self.hop <= 0.0:
            b["vx"] = self.dir * scale * (0.26 + random.random() * 0.20)
            b["vy"] = 0.95 + random.random() * 0.35
            b["wx"] = fx + self.dir * scale * 0.05
            self.hop = 1.0
            self.kicks += 1
            self.screen_glow = 0.4
            for _ in range(7):
                self.dust.append([b["wx"] - self.cam, fy, 0.0, 0.7,
                                   (random.random() - 0.5) * 40.0,
                                   -random.random() * 30.0 - 6.0])

    def _puff(self):
        tf = self.tf
        if not tf:
            return
        for side in ("near", "far"):
            f = tf.get("foot_" + side)
            if not f:
                continue
            sx = self.wx + f[0] * self._scale() * self._flip() - self.cam
            sy = self._ground() - f[1] * self._scale()
            if not (0 <= sx < self.w):
                continue
            self.dust.append([sx, sy, 0.0, 0.85,
                              (random.random() - 0.5) * 26.0,
                              -random.random() * 22.0 - 4.0])

    # ── the places ────────────────────────────────────────────────
    def _scale(self):
        return self.h * 0.66

    def _ground(self):
        return self.h * 0.92

    def _flip(self):
        if self.turning:
            return self.dir * math.cos(self.turn * math.pi)
        return self.dir

    def _desk_wx(self, zone):
        return self._zone_origin(zone) + ZONE_W * 0.60

    def _pc_x(self):
        """Where the figure stands to work: right in front of the keys."""
        scale = self._scale()
        kb = self._desk_wx(self.zone) - 0.16 * scale
        return kb - 0.10 * scale

    def resize(self, w, h):
        self.w, self.h = w, h
        self.em.resize(w, h)
        self.em.fade.fill((150, 150, 150, 255))
        self.pt = pygame.Surface((max(2, w), max(2, h)), 0, 24)
        self.vsurf = pygame.Surface((max(2, w), max(2, h)), pygame.SRCALPHA)
        self.bg = None
        self.cam = self.wx - w * 0.42
        self.x = self.wx - self.cam

    def set_mouse(self, pos):
        try:
            self._mouse = (float(pos[0]), float(pos[1]))
        except Exception:
            pass

    # ── the drawing ────────────────────────────────────────────────
    def draw(self, screen):
        if not self.ok:
            return False
        w, h = self.w, self.h
        hue = self.hue
        lvl = self.level

        # the room
        key = int(hue / 4.0)
        if self.bg is None or self.bg_key != key:
            bg = pygame.Surface((w, h))
            bg.fill((9, 10, 16))
            for i in range(14):
                k = i / 13.0
                col = _hsv(hue + 200.0 + k * 16.0, 0.34,
                           0.026 + 0.040 * (1.0 - abs(k - 0.45)))
                y = int(h * (k * 0.9 - 0.05))
                pygame.draw.rect(bg, col, (0, y, w, int(h * 0.15) + 2))
            fl = int(h * 0.90)
            pygame.draw.rect(bg, _hsv(hue + 190.0, 0.30, 0.055),
                             (0, fl, w, h - fl))
            pygame.draw.line(bg, _hsv(hue + 190.0, 0.35, 0.16),
                             (0, fl), (w, fl))
            self.bg = bg
            self.bg_key = key
        screen.blit(self.bg, (0, 0))

        self.em.fade_step()

        scale = self._scale()
        ground = self._ground()
        flip = self._flip()
        jump = self.hop_h * scale

        tint = np.array(_hsv(hue, 0.52, 1.0), np.float64) / 255.0

        # every line of light of this frame goes on one surface
        vs = self.vsurf
        vs.fill((0, 0, 0, 0))

        # ── the rooms he walks through ────────────────────────────
        self._draw_rooms(vs, scale, ground, tint)

        # ── every particle of the body ─────────────────────────────
        xs = []
        ys = []
        cs = []
        for part in self.parts:
            tf = self.tf.get(part["name"])
            if tf is None:
                continue
            ox, oy, ang = tf
            loc = part["local"]
            ca, sa = math.cos(ang), math.sin(ang)
            fx = ox + ca * loc[:, 0] - sa * loc[:, 1]
            fy = oy + sa * loc[:, 0] + ca * loc[:, 1]
            sx = self.x + fx * scale * flip
            sy = ground - fy * scale - jump
            base = part["col"]
            mix = 0.55 * tint[None, :] + 0.45 * base
            bright = 0.44 + 0.30 * lvl
            if part["name"].endswith("_far"):
                bright *= 0.70
            xs.append(sx)
            ys.append(sy)
            cs.append(mix * bright)

        # ── the head: the real picture ─────────────────────────────
        hx, hy, hang = self.head_tf
        loc = self.head_local
        ca, sa = math.cos(hang), math.sin(hang)
        fx = hx + ca * loc[:, 0] - sa * loc[:, 1]
        fy = hy + sa * loc[:, 0] + ca * loc[:, 1]
        sx = self.x + fx * scale * flip
        sy = ground - fy * scale - jump

        base = self.head_col
        lum = base.mean(axis=1)
        tone = np.clip((lum - 0.02) / 0.55, 0.0, 1.0)
        bright = 0.16 + 0.46 * tone + 0.12 * self.head_warm ** 1.15
        mix = 0.72 * tint[None, :] + 0.28 * base
        xs.append(sx)
        ys.append(sy)
        cs.append(mix * (bright * (1.0 + 0.30 * lvl))[:, None])

        # ── the ball ───────────────────────────────────────────────
        if self.ball is not None:
            b = self.ball
            rot = self.t * 3.0
            cr, sr = math.cos(rot), math.sin(rot)
            pts = self.ball_pts
            fx = (pts[:, 0] * cr - pts[:, 2] * sr) * scale
            fy = pts[:, 1]
            xs.append(b["wx"] - self.cam + fx)
            ys.append(ground - (b["y"] + fy) * scale - jump)
            bc = np.tile(tint[None, :], (len(pts), 1)) * 0.85
            cs.append(bc)

        allx = np.concatenate(xs) if xs else np.zeros(0)
        ally = np.concatenate(ys) if ys else np.zeros(0)
        allc = np.concatenate(cs) if cs else np.zeros((0, 3))

        # the shadow under the feet
        sh_w = int(scale * 0.16)
        shadow = pygame.Surface((sh_w * 2, 22), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (0, 0, 0, 120), (0, 0, sh_w * 2, 22))
        screen.blit(shadow, (int(self.x - sh_w), int(ground - 8)),
                    special_flags=pygame.BLEND_RGBA_ADD)

        # the particles
        self.pt.fill((0, 0, 0))
        if len(allx):
            ix = allx.astype(np.int32)[:, None] + np.array([0, 1, 0, 1])[None, :]
            iy = ally.astype(np.int32)[:, None] + np.array([0, 0, 1, 1])[None, :]
            ix = np.clip(ix, 0, w - 1).ravel()
            iy = np.clip(iy, 0, h - 1).ravel()
            cols = np.repeat(allc * 0.40, 4, axis=0)
            arr = pygame.surfarray.pixels3d(self.pt)
            arr[ix, iy] = (np.clip(cols, 0, 1) * 255).astype(np.uint8)
            del arr
        self.em.surf.blit(self.pt, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        self.em.present(screen)

        # ── the body drawn as light ────────────────────────────────
        alpha = int(90 + 70 * lvl)

        def line(p0, p1, col, a):
            c = (col[0] * a // 255, col[1] * a // 255, col[2] * a // 255)
            pygame.draw.line(vs, c + (a,), p0, p1, 1)

        bone = _hsv(hue + 196.0, 0.40, 1.0)
        warm = _hsv(hue + 8.0, 0.55, 1.0)

        def sp(fx, fy):
            return (int(self.x + fx * scale * flip),
                    int(ground - fy * scale - jump))

        for side in ("far", "near"):
            dim = 0.55 if side == "far" else 1.0
            th = self.tf.get("thigh_" + side)
            sh = self.tf.get("shin_" + side)
            ft = self.tf.get("foot_" + side)
            ua = self.tf.get("ua_" + side)
            la = self.tf.get("la_" + side)
            if th and sh:
                line(sp(th[0], th[1]), sp(sh[0], sh[1]), bone, int(alpha * dim))
            if sh and ft:
                line(sp(sh[0], sh[1]), sp(ft[0], ft[1]), bone, int(alpha * dim))
            if ua and la:
                line(sp(ua[0], ua[1]), sp(la[0], la[1]), warm, int(alpha * dim))
        torso = self.tf.get("torso")
        neck = self.tf.get("neck")
        if torso and neck:
            line(sp(torso[0], torso[1]), sp(neck[0], neck[1]), bone, alpha)
            line(sp(neck[0], neck[1]), sp(self.head_tf[0], self.head_tf[1]),
                 bone, alpha)
        if torso:
            line(sp(torso[0] - 0.09, torso[1] + TORSO_LEN - 0.02),
                 sp(torso[0] + 0.09, torso[1] + TORSO_LEN - 0.02),
                 bone, int(alpha * 0.85))

        # the face on the walking head - the landmarks as light
        self._draw_face(vs, scale, flip, ground, jump, tint)

        # one blit for the rooms, the body and the face
        screen.blit(vs, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

        # the dust
        for d in self.dust:
            life = d[2] / max(1e-4, d[3])
            r = int(2 + 9 * life)
            a = int(70 * (1.0 - life))
            if r > 1 and a > 3:
                pygame.draw.circle(screen, _hsv(hue + 30.0, 0.30, 0.85),
                                   (int(d[0]), int(d[1])), r)
        return True

    # ── the rooms ─────────────────────────────────────────────────
    def _draw_rooms(self, surf, scale, ground, tint):
        """The rooms around the figure, drawn in world space."""
        w, h = self.w, self.h
        hue = self.hue

        def line(p0, p1, col, a, width=1):
            c = (col[0] * a // 255, col[1] * a // 255, col[2] * a // 255)
            pygame.draw.line(surf, c + (a,), p0, p1, width)

        def sx(world_x):
            return int(world_x - self.cam)

        steel = _hsv(hue + 196.0, 0.30, 1.0)
        wood = _hsv(hue + 186.0, 0.35, 1.0)

        # the floor lines move with the camera - it shows he is walking
        step = 130
        first = int(self.cam // step) - 1
        for i in range(first, first + int(w / step) + 3):
            x = sx(i * step)
            line((x, int(ground)), (x, int(ground) + 16), steel, 34)

        z0 = int(self.cam // ZONE_W) - 1
        for zi in range(z0, z0 + 4):
            kind = self._zone_kind(zi)
            o = self._zone_origin(zi)
            if kind == "desk":
                self._draw_desk(surf, surf, sx, o, scale, ground, line,
                                wood, steel, hue)
            elif kind == "lounge":
                self._draw_lounge(surf, surf, sx, o, scale, ground, line,
                                  wood, steel, hue)
            elif kind == "play":
                self._draw_play(surf, surf, sx, o, scale, ground, line,
                                wood, steel, hue)
            else:
                self._draw_window(surf, surf, sx, o, scale, ground, line,
                                  wood, steel, hue)

    def _draw_desk(self, screen, surf, sx, o, scale, ground, line, wood,
                   steel, hue):
        cx = o + ZONE_W * 0.60
        top_y = ground - DESK_H * scale
        # the table
        left = sx(cx - 0.42 * scale)
        right = sx(cx + 0.42 * scale)
        line((left, int(top_y)), (right, int(top_y)), wood, 150, 2)
        line((sx(cx - 0.34 * scale), int(top_y)),
             (sx(cx - 0.34 * scale), int(ground - 6)), steel, 90)
        line((sx(cx + 0.34 * scale), int(top_y)),
             (sx(cx + 0.34 * scale), int(ground - 6)), steel, 90)

        # the monitor
        mw = int(0.30 * scale)
        mh = int(0.24 * scale)
        mx = sx(cx + 0.10 * scale)
        my = int(top_y - mh)
        screen_on = 0.35 + 0.65 * self.screen_glow
        if self.activity == "desk":
            screen_on = 0.85
        pygame.draw.rect(surf, _hsv(hue + 200.0, 0.25, 1.0) +
                         (int(120 * screen_on),),
                         (mx, my, mw, mh), 1)
        # the light of the screen
        glow = pygame.Surface((mw, mh), pygame.SRCALPHA)
        glow.fill(_hsv(hue + 10.0, 0.45, 1.0) + (int(46 * screen_on),))
        surf.blit(glow, (mx, my))
        line((sx(cx + 0.10 * scale), int(top_y)),
             (sx(cx + 0.10 * scale), int(my + mh)), steel, 110)

        # the keyboard
        kbx = sx(cx - 0.22 * scale)
        kby = int(top_y - 6)
        kbw = int(0.24 * scale)
        line((kbx, kby), (kbx + kbw, kby), wood, 130, 2)
        if self.activity == "desk":
            # the keys flicker
            for i in range(9):
                x = kbx + 6 + i * (kbw - 12) // 9
                a = 120 + int(110 * abs(math.sin(self.type_t * 9.0 + i * 0.8)))
                pygame.draw.rect(surf, _hsv(hue + 20.0, 0.5, 1.0) + (a,),
                                 (x, kby - 5, 4, 4))
        # a chair behind the desk
        chx = sx(cx - 0.02 * scale)
        seat = int(ground - SEAT_DROP * scale)
        line((chx, seat), (chx, int(ground - 4)), steel, 70)
        line((chx - int(0.10 * scale), seat),
             (chx + int(0.10 * scale), seat), steel, 90, 2)

    def _draw_lounge(self, screen, surf, sx, o, scale, ground, line, wood,
                     steel, hue):
        """A sofa, a lamp and a rug."""
        cx = o + ZONE_W * 0.52
        seat = int(ground - SEAT_DROP * scale)
        back_h = int(0.26 * scale)
        # the rug
        rug = pygame.Surface((int(0.9 * scale), int(0.10 * scale)),
                             pygame.SRCALPHA)
        pygame.draw.ellipse(rug, _hsv(hue + 20.0, 0.45, 1.0) + (26,),
                            (0, 0, rug.get_width(), rug.get_height()))
        surf.blit(rug, (sx(cx - 0.45 * scale), int(ground - 10)))
        # the sofa
        line((sx(cx - 0.30 * scale), seat), (sx(cx + 0.30 * scale), seat),
             wood, 130, 3)
        line((sx(cx - 0.28 * scale), seat),
             (sx(cx - 0.28 * scale), seat - back_h), wood, 100)
        line((sx(cx + 0.28 * scale), seat),
             (sx(cx + 0.28 * scale), seat - back_h), wood, 100)
        line((sx(cx - 0.28 * scale), seat - back_h),
             (sx(cx + 0.28 * scale), seat - back_h), wood, 80)
        # the lamp
        lx = sx(cx + 0.42 * scale)
        ly = int(ground - 0.62 * scale)
        line((lx, int(ground - 6)), (lx, ly), steel, 90)
        pygame.draw.circle(surf, _hsv(hue + 30.0, 0.4, 1.0) + (120,),
                           (lx, ly - 6), 9, 1)

    def _draw_play(self, screen, surf, sx, o, scale, ground, line, wood,
                   steel, hue):
        """A hoop on the wall, a rug and a couple of cones."""
        cx = o + ZONE_W * 0.50
        rug = pygame.Surface((int(0.8 * scale), int(0.09 * scale)),
                             pygame.SRCALPHA)
        pygame.draw.ellipse(rug, _hsv(hue + 120.0, 0.5, 1.0) + (24,),
                            (0, 0, rug.get_width(), rug.get_height()))
        surf.blit(rug, (sx(cx - 0.40 * scale), int(ground - 8)))
        # the hoop
        hx = sx(cx + 0.34 * scale)
        hy = int(ground - 0.52 * scale)
        pygame.draw.circle(surf, _hsv(hue + 20.0, 0.55, 1.0) + (150,),
                           (hx, hy), int(0.10 * scale), 2)
        # the cones
        for i, dx in enumerate((-0.30, 0.12)):
            cxp = sx(cx + dx * scale)
            pygame.draw.circle(surf, _hsv(hue + 24.0, 0.6, 1.0) + (120,),
                               (cxp, int(ground - 6)), int(0.020 * scale), 1)

    def _draw_window(self, screen, surf, sx, o, scale, ground, line, wood,
                     steel, hue):
        """A big window with the light coming in, and a plant."""
        cx = o + ZONE_W * 0.52
        ww = int(0.44 * scale)
        wh = int(0.34 * scale)
        wx = sx(cx - ww // 2)
        wy = int(ground - 0.78 * scale)
        pygame.draw.rect(surf, _hsv(hue + 200.0, 0.20, 1.0) + (70,),
                         (wx, wy, ww, wh), 2)
        pygame.draw.rect(surf, _hsv(hue + 190.0, 0.25, 1.0) + (34,),
                         (wx, wy, ww, wh))
        line((wx + ww // 2, wy), (wx + ww // 2, wy + wh), steel, 70)
        line((wx, wy + wh // 2), (wx + ww, wy + wh // 2), steel, 70)
        # the light on the floor
        beam = pygame.Surface((ww, int(0.16 * scale)), pygame.SRCALPHA)
        beam.fill(_hsv(hue + 30.0, 0.35, 1.0) + (22,))
        surf.blit(beam, (wx, int(ground - 0.16 * scale)))
        # a plant
        px = sx(cx + 0.40 * scale)
        py = int(ground - 4)
        line((px, py), (px, py - int(0.14 * scale)), wood, 110, 2)
        pygame.draw.circle(surf, _hsv(hue + 90.0, 0.5, 1.0) + (110,),
                           (px, py - int(0.18 * scale)), int(0.05 * scale), 2)

    # ── the face on the walking head ───────────────────────────────
    def _draw_face(self, surf, scale, flip, ground, jump, tint):
        p = self.portrait
        if not p.contours:
            return
        hx, hy, hang = self.head_tf
        ca, sa = math.cos(hang), math.sin(hang)
        per = self._head_per
        chin_y = float(p.anchors["chin"][1])
        face_cx = float(p.anchors["face_cx"])
        hue = self.hue
        base_a = 100 + 60 * self.level
        for name, poly in p.contours:
            # the outline of the head is left out - the face should not sit
            # inside a box, the picture alone is the face
            if name == "oval":
                continue
            lx = (poly[:, 0] - face_cx) * per
            ly = (chin_y - poly[:, 1]) * per
            fx = hx + ca * lx - sa * ly
            fy = hy + sa * lx + ca * ly
            px = (self.x + fx * scale * flip).astype(np.int32)
            py = (ground - fy * scale - jump).astype(np.int32)
            if name == "lips" or name == "lips_in":
                a, c = int(base_a * 0.75), _hsv(hue + 6.0, 0.6, 1.0)
            elif name in ("eye_l", "eye_r"):
                a, c = int(base_a * 1.05), _hsv(hue + 14.0, 0.45, 1.0)
            elif name.startswith("brow"):
                a, c = int(base_a * 0.65), _hsv(hue - 12.0, 0.4, 0.95)
            else:
                a, c = int(base_a * 0.55), _hsv(hue + 196.0, 0.35, 1.0)
            if len(px) > 2:
                cc = (c[0] * a // 255, c[1] * a // 255, c[2] * a // 255)
                pygame.draw.lines(surf, cc + (a,), False,
                                  list(zip(px.tolist(), py.tolist())), 1)
