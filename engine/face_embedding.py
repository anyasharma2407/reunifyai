"""
FaceEmbeddingService: images in, comparable vectors out.

    service = FaceEmbeddingService()
    ea = service.generate_embedding(image_a)
    eb = service.generate_embedding(image_b)
    result = service.compare_embeddings(ea, eb)   # -> similarity 0-100 + detail

What this is and is not
-----------------------
This produces a *descriptor* of a face and compares descriptors. It does not
identify anybody. A high score means two images are geometrically alike, which
in this project is one input to a ranking that a human being then reviews. The
service deliberately exposes no `identify()` or `is_same_person()` call,
because there is no threshold at which this system is entitled to make that
claim.

Why not simply compare pixels
-----------------------------
Two photographs of the same person differ in exposure, white balance, framing,
focus and head angle. Pixel distance is dominated by all of those and barely
registers identity: in this corpus, raw pixel similarity ranks the correct
partner first for well under half of the pairs, because it is really measuring
"were these two photos taken under the same lighting".

The default backend instead computes a histogram of oriented gradients over a
grid of face regions. Gradient *orientation* is invariant to how bright the
image is and largely invariant to contrast, so what survives is the shape of
the edges -- jawline, hairline, brow angle, eye spacing, nose width. This is
the classical face-descriptor family that preceded deep embeddings (it is what
dlib's face detector is built on), and it is strong enough to carry a demo
without downloading a model or calling a paid service.

Backends
--------
The descriptor is pluggable, because the right answer in production is a
learned embedding:

  * ``gradient`` (default) -- pure NumPy, no model file, no network. Always
    available, so the demo cannot fail on a conference network.
  * ``arcface-onnx`` (optional) -- a real learned face embedding. Enabled only
    when onnxruntime is installed and a model file is present; see
    ``ARCFACE_SETUP`` below. Swapping backends changes nothing else in the
    pipeline, which is the point of putting this behind a service boundary.

Calibration
-----------
Raw cosine similarity is not a percentage anyone should read. Gradient
descriptors of two unrelated faces still agree substantially -- both are faces
-- so impostor pairs sit around 0.6-0.8 rather than near zero. Reporting that
as "72% similar" would badly mislead a caseworker.

So the service calibrates against the population it is looking at: it measures
the distribution of similarity between pairs that are overwhelmingly not the
same person, and reports how far above that background a given pair sits. A
score near zero means "as alike as two strangers"; a high score means "far more
alike than strangers are". The calibration constants are written to
data/face_calibration.json by ``python -m engine.generate`` and are reported in
the UI so the number is never mistaken for a probability of identity.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CALIBRATION_PATH = DATA_DIR / "face_calibration.json"

ARCFACE_SETUP = """\
To use a learned embedding instead of the built-in gradient descriptor:

    pip install onnxruntime
    mkdir -p models && curl -L -o models/arcface.onnx <url-to-an-arcface-model>
    export FACE_BACKEND=arcface-onnx

Any ONNX model taking a 112x112x3 normalised face and returning a 512-d vector
will work. Nothing else in the project changes.
"""

# Descriptor geometry. 64x64 with 8px cells gives an 8x8 cell grid; 2x2 blocks
# at stride 1 gives 7x7 blocks of 36 values -> 1764 dimensions.
IMAGE_SIZE = 64
CELL = 8
BINS = 9
BLOCK = 2


# --------------------------------------------------------------------------
# Alignment
# --------------------------------------------------------------------------

def _box_blur(a: np.ndarray, k: int) -> np.ndarray:
    """Separable box blur via cumulative sums, with edge clamping."""
    pad = k // 2
    p = np.pad(a, pad, mode="edge")
    c = np.cumsum(p, axis=0)
    c = np.vstack([c[:1] * 0, c])
    out = (c[k:] - c[:-k]) / k
    c = np.cumsum(out, axis=1)
    c = np.hstack([c[:, :1] * 0, c])
    return (c[:, k:] - c[:, :-k]) / k


def _max_filter(a: np.ndarray, k: int) -> np.ndarray:
    """Dilation by a (2k+1) square, built from shifted maxima."""
    out = a.copy()
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            if dy or dx:
                out = np.maximum(out, np.roll(np.roll(a, dy, axis=0), dx, axis=1))
    return out


def find_eyes(img: Image.Image) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """
    Locate the two eyes, or None if no plausible pair was found.

    An eye is the one place on a face where a bright patch and a dark patch sit
    within a few pixels of each other -- white sclera against black pupil. That
    joint condition is what makes this detector specific: an eyebrow is dark but
    has no bright core, a lit forehead is bright but has no dark core, and only
    the eye scores highly on both. Both terms are measured against a blurred
    copy of the image, so the exposure of the photograph cancels out.

    Candidate peaks are then chosen *as a pair*, scored on how eye-like the two
    points are together: level with each other, plausibly far apart, and
    straddling the middle of the head. Picking the two brightest points
    independently is what an earlier version did, and it failed exactly where
    it mattered -- on a face with a bright forehead it returned two points a
    few pixels apart, which the aligner then read as a face seen from very far
    away and zoomed in on the hairline.
    """
    g = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = g.shape
    blur = _box_blur(g, 15)
    dark = np.clip(blur - g, 0, None)

    # The bright half of the test is restricted to *neutral* highlights. Sclera
    # is very nearly grey, while skin is strongly warm, so this is what
    # separates an eye from the other place a bright patch meets a dark one:
    # the boundary between a lit cheek and dark hair. That edge is level,
    # symmetric and plausibly spaced -- it satisfies every geometric test an
    # eye does -- and before this term the detector would occasionally align a
    # face on its own hairline, putting the pair's facial similarity below what
    # two strangers score.
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    neutral = 1.0 - (mx - mn) / (mx + 1e-3)
    neutral = np.clip((neutral - 0.55) / 0.45, 0.0, 1.0)
    bright = np.clip(g - blur, 0, None) * neutral

    # An eye needs both extremes nearby, so dilate each and take the weaker.
    eyeness = np.minimum(_max_filter(bright, 3), _max_filter(dark, 3))

    band = np.zeros_like(eyeness, dtype=bool)
    band[int(h * 0.16):int(h * 0.66), int(w * 0.08):int(w * 0.92)] = True
    eyeness = np.where(band, eyeness, 0.0)
    if float(eyeness.max()) <= 1e-6:
        return None

    # Horizontal centre of the head, used to prefer a symmetric pair.
    corners = np.concatenate([g[:6, :6].ravel(), g[:6, -6:].ravel(),
                              g[-6:, :6].ravel(), g[-6:, -6:].ravel()])
    fg = np.abs(g - float(np.median(corners))) > 12
    face_cx = float(fg.nonzero()[1].mean()) if fg.any() else w / 2.0

    # Peak-pick with non-maximum suppression.
    work = eyeness.copy()
    radius = max(3, int(w * 0.045))
    peaks: list[tuple[float, float, float]] = []
    for _ in range(14):
        idx = int(work.argmax())
        val = float(work.flat[idx])
        if val <= 0:
            break
        py, px = divmod(idx, w)
        peaks.append((val, float(px), float(py)))
        y0, y1 = max(0, py - radius), min(h, py + radius + 1)
        x0, x1 = max(0, px - radius), min(w, px + radius + 1)
        work[y0:y1, x0:x1] = 0.0
    if len(peaks) < 2:
        return None

    lo_sep, hi_sep = w * 0.14, w * 0.52
    best = None
    for i in range(len(peaks)):
        for j in range(i + 1, len(peaks)):
            v1, x1p, y1p = peaks[i]
            v2, x2p, y2p = peaks[j]
            if x1p > x2p:
                v1, x1p, y1p, v2, x2p, y2p = v2, x2p, y2p, v1, x1p, y1p
            dx = x2p - x1p
            dy = abs(y2p - y1p)
            if not (lo_sep <= dx <= hi_sep) or dy > dx * 0.45:
                continue
            level = 1.0 - min(1.0, dy / (dx * 0.45))
            symmetry = 1.0 - min(1.0, abs((x1p + x2p) / 2 - face_cx) / (w * 0.14))
            score = math.sqrt(v1 * v2) * (0.45 + 0.30 * level + 0.25 * symmetry)
            if best is None or score > best[0]:
                best = (score, (x1p, y1p), (x2p, y2p))
    if best is None:
        return None

    _, rough_l, rough_r = best

    # Refine each centre onto the pupil. The bright/dark peak sits near the eye
    # but drifts with focus; any drift becomes a scale error in the alignment,
    # which attacks the width-to-height ratio the descriptor depends on. The
    # pupil is a compact near-black disc whose darkest point barely moves.
    span = math.hypot(rough_r[0] - rough_l[0], rough_r[1] - rough_l[1])
    win = max(3, int(span * 0.22))
    refined = []
    for (ex, ey) in (rough_l, rough_r):
        x0, x1 = int(max(0, ex - win)), int(min(w, ex + win + 1))
        y0, y1 = int(max(0, ey - win)), int(min(h, ey + win + 1))
        patch = g[y0:y1, x0:x1]
        if patch.size < 9:
            refined.append((ex, ey))
            continue
        sel = patch <= np.percentile(patch, 15.0)
        py, px = sel.nonzero()
        refined.append((x0 + float(px.mean()), y0 + float(py.mean()))
                       if len(px) else (ex, ey))
    return refined[0], refined[1]


def align(img: Image.Image, out: int = IMAGE_SIZE,
          eye_y: float = 0.42, eye_gap: float = 0.34) -> tuple[Image.Image, bool]:
    """
    Warp a photograph into the canonical frame: eyes level, a fixed distance
    apart, at a fixed height.

    This is a similarity transform -- rotate, scale, translate -- and never a
    stretch. Squeezing every head into a square would erase the width-to-height
    ratio of the face, which is one of the strongest things telling two people
    apart; measured on this corpus, doing so costs about a third of the
    correct-match retrievals.

    Returns the aligned image and whether a face was actually located.
    """
    e = find_eyes(img)
    if e is None:
        return img.resize((out, out), Image.LANCZOS), False

    (lx, ly), (rx, ry) = e
    d = math.hypot(rx - lx, ry - ly)
    if d < 4:
        return img.resize((out, out), Image.LANCZOS), False

    th = math.atan2(ry - ly, rx - lx)         # camera roll, removed by the warp
    scale = (eye_gap * out) / d
    cxi, cyi = (lx + rx) / 2, (ly + ry) / 2
    cxo, cyo = out / 2, eye_y * out

    a = math.cos(th) / scale
    b = -math.sin(th) / scale
    dd = math.sin(th) / scale
    e2 = math.cos(th) / scale
    c = cxi - a * cxo - b * cyo
    f = cyi - dd * cxo - e2 * cyo

    warped = img.transform((out, out), Image.AFFINE, (a, b, c, dd, e2, f),
                           resample=Image.BICUBIC, fillcolor=(235, 235, 233))
    return warped, True


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

class GradientDescriptorBackend:
    """Histogram-of-oriented-gradients face descriptor. NumPy only."""

    name = "gradient"
    _hog_dims = (IMAGE_SIZE // CELL - BLOCK + 1) ** 2 * BLOCK * BLOCK * BINS
    _chroma_dims = 4 * 4 * 3
    dimensions = _hog_dims + _chroma_dims

    # How much of the descriptor is colour rather than shape. Colour is a real
    # identity cue but a fragile one -- it is the part a change of lighting
    # attacks first -- so shape is left dominant.
    CHROMA_WEIGHT = 0.40   # measured: 0.55 scores the same, but leaning that
                           # hard on skin tone is both fragile under a change
                           # of lighting and the wrong cue to prioritise here.

    def _hog(self, face: Image.Image) -> np.ndarray:
        a = np.asarray(face.convert("L"), dtype=np.float32)

        # Flatten slow illumination gradients by dividing out a blurred copy:
        # this is what lets one registry shoot under tungsten and the other
        # under daylight without the descriptor noticing.
        a = a - _box_blur(a, 9)
        s = float(a.std())
        if s > 1e-6:
            a = a / s

        gx = np.zeros_like(a)
        gy = np.zeros_like(a)
        gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
        gy[1:-1, :] = a[2:, :] - a[:-2, :]

        mag = np.hypot(gx, gy)
        # Unsigned orientation: a dark-to-light edge and a light-to-dark edge
        # trace the same contour, and which one you get depends on exposure.
        ang = np.mod(np.arctan2(gy, gx), math.pi)

        n = IMAGE_SIZE // CELL
        pos = ang / math.pi * BINS - 0.5
        lo = np.floor(pos).astype(np.int32)
        frac = pos - lo
        hi = np.mod(lo + 1, BINS)
        lo = np.mod(lo, BINS)

        # Soft-assign across the two neighbouring orientation bins so a small
        # rotation slides weight across smoothly instead of jumping.
        hist = np.zeros((n, n, BINS), dtype=np.float32)
        rows = np.broadcast_to(np.arange(IMAGE_SIZE)[:, None] // CELL,
                               (IMAGE_SIZE, IMAGE_SIZE)).ravel()
        cols = np.broadcast_to(np.arange(IMAGE_SIZE)[None, :] // CELL,
                               (IMAGE_SIZE, IMAGE_SIZE)).ravel()
        np.add.at(hist, (rows, cols, lo.ravel()), (mag * (1 - frac)).ravel())
        np.add.at(hist, (rows, cols, hi.ravel()), (mag * frac).ravel())

        # Block normalisation with the usual L2-hys clip, so one high-contrast
        # region (normally the hairline) cannot dominate the vector.
        blocks = []
        for r in range(n - BLOCK + 1):
            for c in range(n - BLOCK + 1):
                b = hist[r:r + BLOCK, c:c + BLOCK].ravel()
                b = b / math.sqrt(float((b ** 2).sum()) + 1e-6)
                b = np.minimum(b, 0.2)
                blocks.append(b / math.sqrt(float((b ** 2).sum()) + 1e-6))

        v = np.concatenate(blocks).astype(np.float32)
        return v / (float(np.linalg.norm(v)) + 1e-9)

    def _chroma(self, face: Image.Image) -> np.ndarray:
        """Coarse colour layout, white-balanced so it survives a warm lens."""
        a = np.asarray(face.convert("RGB"), dtype=np.float32)
        # Grey-world balance: dividing by the image's own mean colour removes
        # the illuminant, leaving the relative colouring of skin against hair.
        a = a / (a.mean(axis=(0, 1), keepdims=True) + 1e-6)
        step = IMAGE_SIZE // 4
        cells = [a[r * step:(r + 1) * step, c * step:(c + 1) * step].mean(axis=(0, 1))
                 for r in range(4) for c in range(4)]
        v = np.concatenate(cells).astype(np.float32)
        return v / (float(np.linalg.norm(v)) + 1e-9)

    def generate(self, img: Image.Image) -> np.ndarray:
        face, _found = align(img)
        w = self.CHROMA_WEIGHT
        v = np.concatenate([self._hog(face) * (1 - w), self._chroma(face) * w])
        return (v / (float(np.linalg.norm(v)) + 1e-9)).astype(np.float32)


class ArcFaceOnnxBackend:
    """A learned face embedding, when one is available locally."""

    name = "arcface-onnx"
    dimensions = 512

    def __init__(self, model_path: Path):
        import onnxruntime
        self.session = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def generate(self, img: Image.Image) -> np.ndarray:
        # Raw 0-255 RGB, and no re-cropping. Both of those were found by
        # measurement rather than assumed, because both failure modes are silent:
        #
        #   * Scaling to [-1, 1] the way many ArcFace exports expect collapses
        #     this one. Every embedding comes out nearly identical -- two
        #     different people scored 0.967 cosine -- so the model looks like it
        #     is working while telling you nothing. On LFW it fell from 94% to
        #     59%.
        #   * The aligner above is tuned for the drawn faces this project
        #     generates, and it mis-locates real photographs. ArcFace was trained
        #     on its own alignment and would rather have the frame it was given
        #     than a worse one; re-cropping cost 15 points on LFW.
        a = np.asarray(img.convert("RGB").resize((112, 112), Image.BILINEAR),
                       dtype=np.float32)
        a = np.transpose(a, (2, 0, 1))[None, ...]
        v = self.session.run(None, {self.input_name: a})[0].ravel().astype(np.float32)
        return v / (float(np.linalg.norm(v)) + 1e-9)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

@dataclass
class Calibration:
    """
    Turns a raw cosine into a number a caseworker can read.

    `background_mean` and `background_sd` describe how similar two *unrelated*
    faces in this corpus look. They are measured from all cross-registry pairs
    without using any ground truth: the overwhelming majority of those pairs
    are different people, so their distribution is the background against which
    a real match has to stand out.

    `midpoint` and `spread` shape the curve that converts "standard deviations
    above background" into 0-100. They are fixed rather than fitted, because
    fitting them to known pairs would let the reported percentage encode the
    answer instead of the evidence.
    """
    background_mean: float
    background_sd: float
    midpoint: float = 0.72
    spread: float = 0.295
    backend: str = "gradient"
    sample_pairs: int = 0

    def to_score(self, cosine: float) -> float:
        z = (cosine - self.background_mean) / max(self.background_sd, 1e-6)
        return 100.0 / (1.0 + math.exp(-(z - self.midpoint) / self.spread))

    def z_of(self, cosine: float) -> float:
        return (cosine - self.background_mean) / max(self.background_sd, 1e-6)

    def as_dict(self) -> dict:
        return {
            "background_mean": round(self.background_mean, 6),
            "background_sd": round(self.background_sd, 6),
            "midpoint": self.midpoint,
            "spread": self.spread,
            "backend": self.backend,
            "sample_pairs": self.sample_pairs,
            "note": "Percentages express how far a pair sits above the "
                    "similarity of two unrelated faces in this corpus. They "
                    "are not probabilities that two records are the same person.",
        }


DEFAULT_CALIBRATION = Calibration(background_mean=0.78, background_sd=0.093)


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------

class FaceEmbeddingService:
    """Images in, comparable vectors out. Never an identity."""

    def __init__(self, backend=None, calibration: Calibration | None = None):
        self.backend = backend or _select_backend()
        self.calibration = calibration or load_calibration(self.backend.name)

    # -- the two operations the rest of the system depends on --------------

    def generate_embedding(self, image: Image.Image | str | Path) -> np.ndarray:
        """Embed one face image. Accepts a PIL image or a path."""
        if not isinstance(image, Image.Image):
            image = Image.open(image)
        return self.backend.generate(image)

    def compare_embeddings(self, a, b) -> dict:
        """
        Compare two embeddings.

        Returns the calibrated similarity, the raw cosine behind it, and how
        many standard deviations above unrelated-face similarity the pair sits.
        All three are surfaced because a single percentage invites more trust
        than this comparison can support.
        """
        a = np.asarray(a, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        if a.shape != b.shape:
            raise ValueError(
                f"embedding shape mismatch: {a.shape} vs {b.shape} — these were "
                f"probably produced by different backends")
        na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
        cosine = float(a @ b) / (na * nb) if na > 1e-9 and nb > 1e-9 else 0.0
        z = self.calibration.z_of(cosine)
        return {
            "similarity": round(self.calibration.to_score(cosine), 1),
            "cosine": round(cosine, 4),
            "z_above_background": round(z, 2),
            "backend": self.backend.name,
            "basis": f"{self.backend.dimensions}-d descriptor, cosine distance",
        }

    def compare_images(self, img_a, img_b) -> dict:
        return self.compare_embeddings(self.generate_embedding(img_a),
                                       self.generate_embedding(img_b))


def _select_backend():
    """
    The gradient descriptor unless ArcFace is asked for by name.

    A learned model is the better matcher on photographs of real people -- 86%
    against 57% on LFW -- and the worse one here, because this project's faces
    are drawn and a model trained on photographs has never seen anything like
    them: 22 of 40 against 34. Neither descriptor is simply better; each wins on
    the domain it was built for.

    So the presence of a model file is not taken as permission to use it. An
    earlier version preferred ArcFace automatically whenever the file existed,
    which meant downloading it to run the benchmark quietly made the demo worse.
    Set FACE_BACKEND=arcface-onnx to ask for it.
    """
    import os
    want = os.environ.get("FACE_BACKEND", "").strip().lower()
    model = Path(__file__).resolve().parent.parent / "models" / "arcface.onnx"
    if want == "arcface-onnx":
        try:
            return ArcFaceOnnxBackend(model)
        except Exception as exc:            # pragma: no cover - optional path
            if want:
                raise RuntimeError(
                    f"FACE_BACKEND=arcface-onnx but the model could not be "
                    f"loaded ({exc}).\n\n{ARCFACE_SETUP}") from exc
    return GradientDescriptorBackend()


def calibrate(embeddings_a, embeddings_b, backend_name: str = "gradient") -> Calibration:
    """
    Measure the background similarity of unrelated faces.

    Uses every A-B pair. A small fraction of them really are the same person,
    which nudges the mean up very slightly and makes the resulting scores
    marginally conservative -- the safe direction for this system to err in.
    """
    A = np.asarray(embeddings_a, dtype=np.float32)
    B = np.asarray(embeddings_b, dtype=np.float32)
    sims = (A @ B.T).ravel()
    return Calibration(
        background_mean=float(sims.mean()),
        background_sd=float(sims.std()) or 0.05,
        backend=backend_name,
        sample_pairs=int(sims.size),
    )


def save_calibration(cal: Calibration) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CALIBRATION_PATH.write_text(json.dumps(cal.as_dict(), indent=2))


def load_calibration(backend_name: str = "gradient") -> Calibration:
    if CALIBRATION_PATH.exists():
        d = json.loads(CALIBRATION_PATH.read_text())
        if d.get("backend") == backend_name:
            return Calibration(
                background_mean=d["background_mean"],
                background_sd=d["background_sd"],
                midpoint=d.get("midpoint", 0.72),
                spread=d.get("spread", 0.295),
                backend=backend_name,
                sample_pairs=d.get("sample_pairs", 0),
            )
    return DEFAULT_CALIBRATION


# --------------------------------------------------------------------------
# Corpus embeddings
# --------------------------------------------------------------------------

EMBEDDINGS_PATH = DATA_DIR / "face_embeddings.json"


class FaceIndex:
    """
    The embeddings held for a corpus, keyed by record id.

    This is the shape the real thing would take. An organisation publishes the
    vectors for the records it holds, not the photographs, and a partner
    organisation can then ask "do you hold anyone who looks like this?" without
    either side transmitting an image of a displaced person. Embeddings are not
    harmless -- they are still biometric data, and this file would need the
    same protection as the photographs -- but they narrow what has to be shared
    to run a comparison.
    """

    def __init__(self, embeddings: dict[str, np.ndarray], meta: dict,
                 service: "FaceEmbeddingService"):
        self.embeddings = embeddings
        self.meta = meta
        self.service = service

    def __contains__(self, record_id: str) -> bool:
        return record_id in self.embeddings

    def get(self, record_id: str):
        return self.embeddings.get(record_id)

    def compare(self, a_id: str, b_id: str) -> dict | None:
        a, b = self.embeddings.get(a_id), self.embeddings.get(b_id)
        if a is None or b is None:
            return None
        return self.service.compare_embeddings(a, b)


def load_face_index(service: "FaceEmbeddingService | None" = None) -> FaceIndex | None:
    """Load data/face_embeddings.json, or None when faces were never generated."""
    if not EMBEDDINGS_PATH.exists():
        return None
    raw = json.loads(EMBEDDINGS_PATH.read_text())
    service = service or FaceEmbeddingService()
    meta = raw.get("_meta", {})
    if meta.get("backend") and meta["backend"] != service.backend.name:
        raise RuntimeError(
            f"data/face_embeddings.json was written by the "
            f"'{meta['backend']}' backend but the active backend is "
            f"'{service.backend.name}'. Re-run: python -m engine.generate")
    return FaceIndex(
        {k: np.asarray(v, dtype=np.float32) for k, v in raw["embeddings"].items()},
        meta, service)
