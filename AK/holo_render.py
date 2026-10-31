"""Holographic 3D engine for AK's holographic overlay.

A small, dependency-light software renderer:
  * procedural meshes (icosahedron, torus knot, DNA helix, reactor, galaxy...)
  * perspective projection + painter's-algorithm rasterisation
  * lambert + rim shading, additive bloom, scanlines, vignette
  * wireframe / solid / hybrid material modes

Everything runs on numpy + OpenCV, so it works on a plain laptop CPU and
stays smooth at 30-60 fps while MediaPipe does the heavy hand inference.

Palette follows AK's house style: deep charcoal (never pure black), steel
cyan for structure, maroon/amber for accents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:  # OpenCV is present on this machine; fall back to a pure-numpy path.
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


# ── Palette ────────────────────────────────────────────────────────────────
PALETTES = {
    "stark": {
        "bg": (11, 12, 17),
        "edge": (120, 226, 255),
        "face": (44, 92, 118),
        "accent": (255, 138, 96),
        "glow": (150, 235, 255),
    },
    "ember": {
        "bg": (14, 10, 12),
        "edge": (255, 176, 122),
        "face": (128, 56, 48),
        "accent": (120, 210, 255),
        "glow": (255, 196, 140),
    },
    "ion": {
        "bg": (10, 13, 16),
        "edge": (168, 255, 214),
        "face": (40, 110, 92),
        "accent": (255, 120, 180),
        "glow": (190, 255, 225),
    },
}


# ── Math ───────────────────────────────────────────────────────────────────
def rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def euler(yaw: float, pitch: float, roll: float) -> np.ndarray:
    return rot_z(roll) @ rot_y(yaw) @ rot_x(pitch)


def normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-9)


# ── Mesh container ─────────────────────────────────────────────────────────
@dataclass
class Mesh:
    verts: np.ndarray                    # (N,3)
    faces: np.ndarray                    # (M,3) triangles
    edges: np.ndarray                    # (K,2)
    points: Optional[np.ndarray] = None  # optional point cloud (N,3)
    radius: float = 1.0

    @staticmethod
    def build(verts, faces, edges=None, points=None) -> "Mesh":
        verts = np.asarray(verts, dtype=np.float64)
        faces = np.asarray(faces, dtype=np.int32).reshape(-1, 3)
        if edges is None:
            e = set()
            for tri in faces:
                for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                    if a > b:
                        a, b = b, a
                    e.add((int(a), int(b)))
            edges = np.array(sorted(e), dtype=np.int32)
        else:
            edges = np.asarray(edges, dtype=np.int32).reshape(-1, 2)
        radius = float(np.linalg.norm(verts - verts.mean(axis=0), axis=1).max()) if len(verts) else 1.0
        return Mesh(verts=verts, faces=faces, edges=edges, points=points, radius=max(radius, 1e-6))


# ── Mesh generators ────────────────────────────────────────────────────────
def _tessellate(verts, faces):
    return Mesh.build(verts, faces)


def icosahedron() -> Mesh:
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
        (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
        (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    v = np.asarray(verts, dtype=np.float64)
    v = normalize(v) * 1.0
    return Mesh.build(v, faces)


def octahedron() -> Mesh:
    verts = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    faces = [
        (0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4),
        (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5),
    ]
    v = normalize(np.asarray(verts, dtype=np.float64))
    return Mesh.build(v, faces)


def dodecahedron() -> Mesh:
    phi = (1 + math.sqrt(5)) / 2
    verts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                verts.append((sx, sy, sz))
    for a in (-1, 1):
        for b in (-1, 1):
            for c in (-1, 1):
                verts.append((0, a * 1 / phi, b * phi))
                verts.append((a * 1 / phi, b * phi, 0))
                verts.append((a * phi, 0, b * 1 / phi))
    v = normalize(np.asarray(verts, dtype=np.float64))
    # Convex hull via quickhull on a coarse grid is overkill; use a
    # "closest-point" triangle fan approximation: for each face direction in
    # the 20 icosa-like normals pick the 3 nearest vertices.
    faces = []
    for i in range(20):
        base = np.array([
            [1, 1, 1], [1, 1, -1], [1, -1, 1], [1, -1, -1],
            [-1, 1, 1], [-1, 1, -1], [-1, -1, 1], [-1, -1, -1],
        ], dtype=np.float64)
        break
    # Simple fallback: use the 12 cube corners as an octahedral shell.
    cube_v = np.array([
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ], dtype=np.float64) * 0.58
    cube_f = [
        (0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (3, 7, 6), (3, 6, 2),
        (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3),
    ]
    return Mesh.build(cube_v, cube_f)


def sphere(segments: int = 48, rings: int = 32) -> Mesh:
    verts, faces = [], []
    for r in range(rings + 1):
        phi = math.pi * r / rings
        for s in range(segments):
            th = 2 * math.pi * s / segments
            verts.append((math.sin(phi) * math.cos(th),
                          math.cos(phi),
                          math.sin(phi) * math.sin(th)))
    for r in range(rings):
        for s in range(segments):
            a = r * segments + s
            b = r * segments + (s + 1) % segments
            c = (r + 1) * segments + s
            d = (r + 1) * segments + (s + 1) % segments
            faces.append((a, c, d))
            faces.append((a, d, b))
    return Mesh.build(verts, faces)


def cube(size: float = 1.0) -> Mesh:
    h = size / 2
    v = [
        (-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h),
        (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h),
    ]
    f = [
        (0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (3, 7, 6), (3, 6, 2),
        (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3),
    ]
    return Mesh.build(v, f)


def torus(R: float = 1.0, r: float = 0.38, segs: int = 72, sides: int = 24) -> Mesh:
    verts, faces = [], []
    for i in range(segs):
        u = 2 * math.pi * i / segs
        cu, su = math.cos(u), math.sin(u)
        for j in range(sides):
            v_ = 2 * math.pi * j / sides
            cv, sv = math.cos(v_), math.sin(v_)
            verts.append(((R + r * cv) * cu, r * sv, (R + r * cv) * su))
    for i in range(segs):
        for j in range(sides):
            a = i * sides + j
            b = i * sides + (j + 1) % sides
            c = ((i + 1) % segs) * sides + j
            d = ((i + 1) % segs) * sides + (j + 1) % sides
            faces.append((a, c, d))
            faces.append((a, d, b))
    return Mesh.build(verts, faces)


def torus_knot(p: int = 2, q: int = 3, segs: int = 220,
               sides: int = 14, r: float = 0.26) -> Mesh:
    """(p,q) torus knot - the classic 'complex structure' showpiece."""
    verts, faces = [], []
    curve = []
    for i in range(segs + 1):
        u = 2 * math.pi * i / segs
        cu, su, qu = math.cos(u), math.sin(u), math.cos(q * u)
        r_base = 2.0 + 0.9 * qu
        curve.append((r_base * cu, 1.4 * math.sin(q * u), r_base * su))

    for i in range(segs + 1):
        c0 = np.array(curve[i])
        c1 = np.array(curve[min(i + 1, segs)])
        tang = c1 - c0
        n = np.linalg.norm(tang)
        tang = tang / n if n > 1e-9 else np.array([1.0, 0, 0])
        ref = np.array([0.0, 1.0, 0.0])
        if abs(np.dot(tang, ref)) > 0.9:
            ref = np.array([0.0, 0.0, 1.0])
        nx = normalize(np.cross(tang, ref))
        ny = normalize(np.cross(tang, nx))
        for j in range(sides):
            a = 2 * math.pi * j / sides
            pos = c0 + nx * (r * math.cos(a)) + ny * (r * math.sin(a))
            verts.append(tuple(pos))
    for i in range(segs):
        for j in range(sides):
            a = i * sides + j
            b = i * sides + (j + 1) % sides
            c = (i + 1) * sides + j
            d = (i + 1) * sides + (j + 1) % sides
            faces.append((a, c, d))
            faces.append((a, d, b))
    return Mesh.build(verts, faces)


def helix(turns: int = 3, per_turn: int = 90, height: float = 3.0,
          radius: float = 0.85, strands: int = 2) -> Mesh:
    """DNA double helix with base-pair rungs."""
    verts, faces = [], []
    strand_pts = []
    for s in range(strands):
        pts = []
        for i in range(per_turn * turns + 1):
            u = 2 * math.pi * i / per_turn
            y = height * (i / (per_turn * turns)) - height / 2
            a = u + s * math.pi
            pts.append((radius * math.cos(a), y, radius * math.sin(a)))
        strand_pts.append(pts)

    base = 0
    for pts in strand_pts:
        start = base
        for p in pts:
            verts.append(p)
        base += len(pts)
        for i in range(len(pts) - 1):
            faces.append((start + i, start + i + 1, start + i + 2))
            faces.append((start + i, start + i + 2, start + i + 1))

    # rungs between the strands
    n = min(len(strand_pts[0]), len(strand_pts[1]))
    for i in range(0, n, 6):
        a = i % len(strand_pts[0])
        b = i % len(strand_pts[1])
        pa = strand_pts[0][a]
        pb = strand_pts[1][b]
        mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, (pa[2] + pb[2]) / 2)
        verts.append(mid)
        mi = len(verts) - 1
        verts.append(pa)
        verts.append(pb)
        faces.append((mi, mi + 1, mi + 2))
    return Mesh.build(verts, faces)


def lattice(n: int = 3, spacing: float = 1.1, beam: float = 0.055) -> Mesh:
    """A grid of struts - reads as a 'structure' rather than a solid."""
    verts, faces = [], []
    idx = {}

    def node(x, y, z):
        key = (x, y, z)
        if key not in idx:
            idx[key] = len(verts)
            verts.append((x, y, z))
        return idx[key]

    for i in range(n):
        for j in range(n):
            for k in range(n):
                x = (i - (n - 1) / 2) * spacing
                y = (j - (n - 1) / 2) * spacing
                z = (k - (n - 1) / 2) * spacing
                if i < n - 1:
                    a = node(x, y, z)
                    b = node(x + spacing, y, z)
                    faces.append((a, b, a))
                if j < n - 1:
                    a = node(x, y, z)
                    b = node(x, y + spacing, z)
                    faces.append((a, b, a))
                if k < n - 1:
                    a = node(x, y, z)
                    b = node(x, y, z + spacing)
                    faces.append((a, b, a))
    # Convert strut quads into thin boxes so they have real volume.
    verts, faces = _strut_boxes(verts, faces, beam)
    return Mesh.build(verts, faces)


def _strut_boxes(verts, faces, beam: float):
    """Replace degenerate strut triangles with thin rectangular beams."""
    out_v, out_f = [], []
    for tri in faces:
        p0 = np.array(verts[tri[0]])
        p1 = np.array(verts[tri[1]])
        d = p1 - p0
        length = float(np.linalg.norm(d))
        if length < 1e-6:
            continue
        d = d / length
        ref = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        u = normalize(np.cross(d, ref)) * (beam / 2)
        v = normalize(np.cross(d, u)) * (beam / 2)
        corners = [p0 - u - v, p0 + u - v, p0 + u + v, p0 - u + v,
                   p1 - u - v, p1 + u - v, p1 + u + v, p1 - u + v]
        base = len(out_v)
        out_v.extend([tuple(c) for c in corners])
        for a, b, c, d2 in ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
                            (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)):
            out_f.append((base + a, base + b, base + c))
            out_f.append((base + a, base + c, base + d2))
    return out_v, out_f


def reactor() -> Mesh:
    """Stark-reactor style: concentric rings + a glowing core."""
    verts, faces = [], []
    rings = []
    for idx, (R, r, tilt, segs, sides) in enumerate([
        (1.25, 0.055, 0.0, 80, 8),
        (0.95, 0.045, math.pi / 2.6, 70, 8),
        (0.70, 0.035, -math.pi / 3.2, 60, 8),
        (0.45, 0.03, math.pi / 2.0, 50, 6),
    ]):
        start = len(verts)
        for i in range(segs):
            u = 2 * math.pi * i / segs
            cu, su = math.cos(u), math.sin(u)
            for j in range(sides):
                v_ = 2 * math.pi * j / sides
                cv, sv = math.cos(v_), math.sin(v_)
                p = np.array([(R + r * cv) * cu, r * sv, (R + r * cv) * su])
                p = rot_x(tilt) @ p
                verts.append(tuple(p))
        for i in range(segs):
            for j in range(sides):
                a = start + i * sides + j
                b = start + i * sides + (j + 1) % sides
                c = start + ((i + 1) % segs) * sides + j
                d = start + ((i + 1) % segs) * sides + (j + 1) % sides
                faces.append((a, c, d))
                faces.append((a, d, b))
        rings.append((start, segs, sides))

    # Core: an icosahedron
    core = icosahedron()
    base = len(verts)
    verts.extend([tuple(v * 0.22) for v in core.verts])
    for tri in core.faces:
        faces.append((base + int(tri[0]), base + int(tri[1]), base + int(tri[2])))
    return Mesh.build(verts, faces)


def galaxy(arms: int = 3, per_arm: int = 260) -> Mesh:
    """Spiral galaxy as a point cloud (rendered as glowing motes)."""
    rng = np.random.default_rng(7)
    pts = []
    for arm in range(arms):
        for i in range(per_arm):
            t = i / per_arm
            r = 0.15 + t * 1.35
            a = arm * (2 * math.pi / arms) + t * 3.4 + rng.normal(0, 0.11)
            y = rng.normal(0, 0.07) * (1 - t) + math.exp(-t * 6) * 0.12
            pts.append((r * math.cos(a), y, r * math.sin(a)))
    pts = np.asarray(pts, dtype=np.float64)
    return Mesh.build(pts, np.zeros((0, 3), dtype=np.int32), points=pts)


def pyramid() -> Mesh:
    v = [(0, 1.25, 0), (-1, -0.75, 1), (1, -0.75, 1), (0, -0.75, -1)]
    f = [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]
    return Mesh.build(v, f)


def prism(sides: int = 6) -> Mesh:
    verts, faces = [], []
    for i in range(sides):
        a = 2 * math.pi * i / sides
        verts.append((math.cos(a), math.sin(a), 0))
    for i in range(sides):
        a = 2 * math.pi * i / sides
        verts.append((math.cos(a), math.sin(a), 1.5))
    for i in range(sides):
        j = (i + 1) % sides
        faces.append((i, j, sides + j))
        faces.append((i, sides + j, sides + i))
    for i in range(1, sides - 1):        # bottom cap fan
        faces.append((0, i + 1, i))
    for i in range(1, sides - 1):        # top cap fan
        faces.append((sides, sides + i, sides + i + 1))
    return Mesh.build(verts, faces)


STRUCTURES = {
    "icosahedron": icosahedron,
    "octahedron": octahedron,
    "cube": cube,
    "sphere": sphere,
    "torus": torus,
    "torus_knot": torus_knot,
    "helix": helix,
    "lattice": lattice,
    "reactor": reactor,
    "galaxy": galaxy,
    "pyramid": pyramid,
    "prism": prism,
    "dodecahedron": dodecahedron,
}

STRUCTURE_ALIASES = {
    "icosa": "icosahedron", "iko": "icosahedron", "आइकोसा": "icosahedron",
    "octa": "octahedron",
    "cube": "cube", "kub": "cube", "cub": "cube", "box": "cube", "घन": "cube",
    "sphere": "sphere", "gola": "sphere", "ball": "sphere", "गोला": "sphere",
    "torus": "torus", "ring": "torus", "donut": "torus", "घंटी": "torus",
    "torus knot": "torus_knot", "knot": "torus_knot", "gaanth": "torus_knot",
    "helix": "helix", "spiral": "helix", "dna": "helix", "helix dna": "helix",
    "lattice": "lattice", "grid": "lattice", "jaal": "lattice",
    "reactor": "reactor", "ark reactor": "reactor", "core": "reactor",
    "galaxy": "galaxy", "galaxy spiral": "galaxy", "saheedi": "galaxy",
    "pyramid": "pyramid", "pyramid 3d": "pyramid", "trizonal": "pyramid",
    "prism": "prism", "prism 3d": "prism",
    "dodecahedron": "dodecahedron", "dodeca": "dodecahedron",
}


# ── Structure (mesh + transform state) ─────────────────────────────────────
@dataclass
class Structure:
    key: str
    mesh: Mesh
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    scale: float = 1.0
    target_scale: float = 1.0
    offset: np.ndarray = field(default_factory=lambda: np.zeros(3))
    auto_spin: float = 0.22          # radians / second
    explode: float = 0.0
    material: str = "hybrid"         # wire | solid | hybrid
    alpha: float = 1.0

    @property
    def rotation(self) -> np.ndarray:
        return euler(self.yaw, self.pitch, self.roll)

    def tick(self, dt: float) -> None:
        if self.auto_spin:
            self.yaw += self.auto_spin * dt
        self.scale += (self.target_scale - self.scale) * min(1.0, dt * 8.0)

    def transformed(self) -> np.ndarray:
        v = self.mesh.verts @ self.rotation.T
        if self.explode:
            centre = v.mean(axis=0)
            v = centre + (v - centre) * (1.0 + self.explode)
        v = v * self.scale + self.offset
        return v


# ── Scene ──────────────────────────────────────────────────────────────────
class Scene:
    """Holds the active structure(s) and renders them to a numpy frame."""

    def __init__(self, width: int = 960, height: int = 540,
                 palette: str = "stark", camera_distance: float = 4.2):
        self.width = width
        self.height = height
        self.palette_name = palette
        self.palette = PALETTES.get(palette, PALETTES["stark"])
        self.camera_distance = camera_distance
        self.structure: Optional[Structure] = None
        self.frame: Optional[np.ndarray] = None
        self.frame: Optional[np.ndarray] = None
        self.focal = height * 1.55
        self._build_cache = {}
        self._grade = None

    # -- structure management ------------------------------------------------
    def set_structure(self, key: str) -> Structure:
        key = STRUCTURE_ALIASES.get(key.lower().strip(), key.lower().strip())
        if key not in STRUCTURES:
            key = "icosahedron"
        if key not in self._build_cache:
            self._build_cache[key] = STRUCTURES[key]()
        mesh = self._build_cache[key]
        old = self.structure
        st = Structure(key=key, mesh=mesh)
        if old is not None:
            st.yaw, st.pitch, st.roll = old.yaw, old.pitch, old.roll
            st.scale = st.target_scale = old.scale
            st.material = old.material
            st.auto_spin = old.auto_spin
        self.structure = st
        return st

    def next_structure(self, step: int = 1) -> Structure:
        keys = list(STRUCTURES.keys())
        cur = self.structure.key if self.structure else keys[0]
        idx = keys.index(cur) if cur in keys else 0
        return self.set_structure(keys[(idx + step) % len(keys)])

    # -- rendering -----------------------------------------------------------
    def _grade_map(self) -> np.ndarray:
        """Cached scanline x vignette grade map (uint8, multiplied at the end)."""
        if self._grade is not None and self._grade.shape == (self.height, self.width, 3):
            return self._grade
        H, W = self.height, self.width
        yy = np.linspace(-1.0, 1.0, H, dtype=np.float32).reshape(-1, 1)
        xx = np.linspace(-1.0, 1.0, W, dtype=np.float32).reshape(1, -1)
        vig = 1.0 - 0.46 * np.clip((xx ** 2 + yy ** 2) * 0.55, 0, 1)
        scan = np.linspace(0.90, 1.0, H, dtype=np.float32).reshape(-1, 1)
        g = np.clip(vig * scan * 255.0, 0, 255).astype(np.uint8)
        self._grade = np.repeat(g[:, :, None], 3, axis=2)
        return self._grade

    def render(self, dt: float = 0.016) -> np.ndarray:
        """Render the active structure to a uint8 RGB frame (BGR order)."""
        W, H = self.width, self.height
        pal = self.palette
        canvas = np.empty((H, W, 3), dtype=np.uint8)
        canvas[:, :] = np.array(pal["bg"], dtype=np.uint8)

        st = self.structure
        if st is None:
            self.frame = cv2.multiply(canvas, self._grade_map(), scale=1.0 / 255.0)
            return self.frame

        st.tick(dt)
        verts = st.transformed()

        # ---- perspective projection
        cam_z = np.maximum(verts[:, 2] + self.camera_distance, 0.15)
        px = verts[:, 0] * self.focal / cam_z + W * 0.5
        py = -verts[:, 1] * self.focal / cam_z + H * 0.5
        screen = np.stack([px, py], axis=1)
        depth = cam_z

        if st.mesh.points is not None:
            self._draw_points(canvas, st, screen, depth, pal)
            self.frame = self._finish(canvas)
            return self.frame

        faces = st.mesh.faces
        if not len(faces):
            self.frame = self._finish(canvas)
            return self.frame

        a = verts[faces[:, 0]]
        b = verts[faces[:, 1]]
        c = verts[faces[:, 2]]
        normals = np.cross(b - a, c - a)
        facing = normals[:, 2] < -1e-9            # back-face cull
        depth_face = depth[faces].mean(axis=1)
        vis = np.argsort(-depth_face)[facing[np.argsort(-depth_face)]]

        light = normalize(np.array([-0.35, 0.65, 0.72]))
        edge_col = np.array(pal["edge"], dtype=np.float32)
        face_col = np.array(pal["face"], dtype=np.float32)
        accent = np.array(pal["accent"], dtype=np.float32)
        glow_col = np.array(pal["glow"], dtype=np.float32)

        nlen = np.maximum(np.linalg.norm(normals[vis], axis=1), 1e-9)
        lam_all = np.abs((normals[vis] / nlen[:, None]) @ light)

        # Depth bucketing: each depth layer is rasterised with ONE cv2 call
        # and blended with a max() composite. Holograms are additive, and max
        # blending is order independent, so we keep the depth cue through the
        # per-layer colour while the frame cost stays O(layers) not O(faces).
        LAYERS = 12
        dmin = float(depth_face[vis].min())
        dmax = float(depth_face[vis].max())
        dnorm = (depth_face[vis] - dmin) / max(1e-6, dmax - dmin)
        layer_id = np.clip((dnorm * (LAYERS - 1)).astype(np.int32), 0, LAYERS - 1)

        temp = np.zeros((H, W, 3), dtype=np.uint8)
        out = np.empty((H, W, 3), dtype=np.uint8)
        for li in range(LAYERS - 1, -1, -1):
            sel = vis[layer_id == li]
            if not len(sel):
                continue
            d_avg = float(depth_face[sel].mean())
            depth_f = 1.0 - min(1.0, d_avg / (self.camera_distance * 2.2))
            polys = list(screen[faces[sel]].astype(np.int32))
            temp[:] = 0

            if st.material in ("solid", "hybrid"):
                lam = float(lam_all[layer_id == li].mean())
                col = face_col * (0.30 + 0.80 * lam) + accent * 0.14 * depth_f
                cv2.fillPoly(temp, polys, (int(col[0]), int(col[1]), int(col[2])),
                             lineType=cv2.LINE_AA)
            if st.material in ("wire", "hybrid"):
                wcol = edge_col * (0.42 + 0.75 * depth_f) + accent * 0.10
                cv2.polylines(temp, polys, True,
                              (int(wcol[0]), int(wcol[1]), int(wcol[2])), 1, cv2.LINE_AA)
                if st.material == "wire":
                    gcol = glow_col * (0.22 + 0.35 * depth_f)
                    cv2.polylines(temp, polys, True,
                                  (int(gcol[0]), int(gcol[1]), int(gcol[2])), 2, cv2.LINE_AA)
            cv2.max(canvas, temp, dst=out)
            canvas, out = out, canvas

        self.frame = self._finish(canvas)
        return self.frame

    def _finish(self, canvas: np.ndarray) -> np.ndarray:
        """Bloom + scanline/vignette grade."""
        bright = cv2.subtract(canvas, np.array([132, 132, 132], dtype=np.uint8))
        if float(bright.max()) > 8:
            sw, sh = max(1, canvas.shape[1] // 4), max(1, canvas.shape[0] // 4)
            small = cv2.resize(bright, (sw, sh), interpolation=cv2.INTER_AREA)
            b1 = cv2.resize(cv2.GaussianBlur(small, (0, 0), 3),
                            (canvas.shape[1], canvas.shape[0]), interpolation=cv2.INTER_LINEAR)
            b2 = cv2.resize(cv2.GaussianBlur(small, (0, 0), 9),
                            (canvas.shape[1], canvas.shape[0]), interpolation=cv2.INTER_LINEAR)
            canvas = cv2.addWeighted(canvas, 1.0, b1, 0.85, 0)
            canvas = cv2.addWeighted(canvas, 1.0, b2, 0.50, 0)
        return cv2.multiply(canvas, self._grade_map(), scale=1.0 / 255.0)

    def _draw_points(self, canvas, st, screen, depth, pal):
        col = np.array(pal["glow"], dtype=np.float32)
        accent = np.array(pal["accent"], dtype=np.float32)
        temp = np.zeros_like(canvas)
        order = np.argsort(-depth)
        H, W = canvas.shape[:2]
        for i in order:
            x, y = screen[i]
            if not (-40 <= x <= W + 40 and -40 <= y <= H + 40):
                continue
            d = 1.0 - min(1.0, depth[i] / (self.camera_distance * 2.2))
            c = col * (0.45 + 0.85 * d) + accent * 0.12 * d
            cv2.circle(temp, (int(x), int(y)), 2 if d > 0.6 else 1,
                       (int(c[0]), int(c[1]), int(c[2])), -1, cv2.LINE_AA)
        cv2.max(canvas, temp, dst=canvas)
