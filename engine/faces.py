"""
Synthetic face generation for the Reunification Engine.

EVERY FACE IN THIS PROJECT IS DRAWN BY THIS FILE FROM A SEEDED NUMBER.

No photograph of any real person is used, downloaded, or shipped. The faces are
deliberately illustrative rather than photoreal: a reviewer glancing at the demo
must never be able to mistake one of these images for a real displaced person,
and a screenshot of the demo must never resemble real biometric data. Drawn
faces make that guarantee structural rather than a promise in a README.

The two-stage model
-------------------
Generation is split in two, and the split is the whole point:

  * a *latent* is the person -- roughly twenty numbers describing face geometry,
    colouring and hair. It is derived deterministically from the person id, so
    the same synthetic person always has the same face.

  * a *capture* is one photograph of that person -- a particular lighting
    condition, camera angle, focus and sensor noise. Each registry photographs
    the person independently, so Registry A and Registry B hold two genuinely
    different images of the same face.

This matters because it is what makes the face matching honest. If both
registries held the identical PNG, cosine similarity would be exactly 1.0 and
the demo would prove nothing. Because each side holds its own capture, the
embedder has to recover identity across lighting, pose and noise -- which is
the actual problem a face-matching system solves.

The embedder never sees a latent. It is handed pixels, like any real system.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFilter

# Render at 4x and downsample: cheap, reliable antialiasing for shapes drawn
# with Pillow, which has no native antialiased polygon fill.
SUPERSAMPLE = 4
OUTPUT_SIZE = 256


# --------------------------------------------------------------------------
# Deterministic pseudo-randomness
# --------------------------------------------------------------------------

def _stream(*parts: object) -> list[float]:
    """
    A reproducible stream of floats in [0, 1) derived from the given parts.

    Uses BLAKE2b rather than random.Random so that a person's face depends only
    on their id and never on how many other people happened to be generated
    first. Regenerating the corpus with a different size leaves every existing
    face unchanged.
    """
    seed = "|".join(str(p) for p in parts).encode("utf-8")
    out: list[float] = []
    counter = 0
    while len(out) < 64:
        digest = hashlib.blake2b(seed + counter.to_bytes(4, "big"), digest_size=64).digest()
        for i in range(0, 64, 4):
            out.append(int.from_bytes(digest[i:i + 4], "big") / 2**32)
        counter += 1
    return out


def _spread(u: float, lo: float, hi: float) -> float:
    return lo + u * (hi - lo)


# --------------------------------------------------------------------------
# The latent: who this synthetic person looks like
# --------------------------------------------------------------------------

@dataclass
class FaceLatent:
    """Geometry and colouring of one fictional face."""
    person_id: str
    skin: tuple[int, int, int]
    hair: tuple[int, int, int]
    iris: tuple[int, int, int]
    face_width: float
    face_height: float
    jaw_taper: float
    chin_round: float
    eye_spacing: float
    eye_size: float
    eye_tilt: float
    brow_height: float
    brow_thickness: float
    brow_angle: float
    nose_width: float
    nose_length: float
    mouth_width: float
    mouth_fullness: float
    hairline: float
    hair_style: int
    facial_hair: float
    ear_size: float

    def as_dict(self) -> dict:
        return {"person_id": self.person_id, "note": "synthetic, procedurally drawn"}


SKIN_TONES = [
    (243, 213, 188), (236, 198, 167), (223, 178, 142), (206, 158, 120),
    (186, 137, 100), (163, 115, 82), (134, 92, 64), (106, 72, 50),
]
HAIR_TONES = [
    (28, 22, 20), (44, 32, 26), (62, 42, 30), (88, 60, 38),
    (120, 84, 50), (150, 118, 74), (90, 88, 92), (150, 148, 152),
]
IRIS_TONES = [
    (62, 42, 28), (86, 58, 34), (44, 66, 52), (52, 78, 96),
    (34, 30, 28), (98, 84, 56),
]


def latent_for(person_id: str, sex: str, birth_year: int) -> FaceLatent:
    """Derive the fixed appearance of one synthetic person."""
    u = _stream("latent", person_id)
    age = max(0, 2026 - birth_year)
    female = (sex or "").upper().startswith("F")

    # Older synthetic people get greyer hair; this is the only place age
    # influences appearance, and it is a visual cue only -- the matcher scores
    # age from the recorded birth date, never from the picture.
    grey_pull = max(0.0, min(0.7, (age - 45) / 55.0))
    hair_idx = int(u[1] * len(HAIR_TONES))
    if u[2] < grey_pull:
        hair_idx = 6 + int(u[3] * 2)

    return FaceLatent(
        person_id=person_id,
        skin=SKIN_TONES[int(u[0] * len(SKIN_TONES))],
        hair=HAIR_TONES[min(hair_idx, len(HAIR_TONES) - 1)],
        iris=IRIS_TONES[int(u[4] * len(IRIS_TONES))],
        face_width=_spread(u[5], 0.60, 0.76),
        face_height=_spread(u[6], 0.82, 0.98),
        jaw_taper=_spread(u[7], 0.52, 0.86),
        chin_round=_spread(u[8], 0.30, 0.85),
        eye_spacing=_spread(u[9], 0.30, 0.40),
        eye_size=_spread(u[10], 0.072, 0.108),
        eye_tilt=_spread(u[11], -0.10, 0.12),
        brow_height=_spread(u[12], 0.055, 0.105),
        brow_thickness=_spread(u[13], 0.012, 0.030),
        brow_angle=_spread(u[14], -0.16, 0.18),
        nose_width=_spread(u[15], 0.090, 0.150),
        nose_length=_spread(u[16], 0.130, 0.210),
        mouth_width=_spread(u[17], 0.150, 0.230),
        mouth_fullness=_spread(u[18], 0.030, 0.062),
        hairline=_spread(u[19], 0.10, 0.24),
        hair_style=int(u[20] * (6 if female else 5)),
        facial_hair=0.0 if female else (u[21] if u[22] < 0.45 else 0.0),
        ear_size=_spread(u[23], 0.055, 0.085),
    )


# --------------------------------------------------------------------------
# The capture: one photograph of that person
# --------------------------------------------------------------------------

@dataclass
class Capture:
    """Conditions under which one registry photographed the person."""
    brightness: float = 1.0
    contrast: float = 1.0
    warmth: float = 0.0
    yaw: float = 0.0          # head turn, as a horizontal squeeze
    roll: float = 0.0         # camera tilt, in degrees
    scale: float = 1.0
    shift_x: float = 0.0
    shift_y: float = 0.0
    blur: float = 0.0
    noise: float = 0.0
    backdrop: tuple[int, int, int] = (232, 232, 230)


def capture_for(person_id: str, registry: str, intensity: float = 1.0) -> Capture:
    """
    Invent the capture conditions for one registry's photograph.

    `intensity` scales how adverse the conditions are. Registry B is the field
    enumeration desk in this scenario, so its captures are given rougher
    conditions than Registry A's reception-centre photographs.
    """
    u = _stream("capture", person_id, registry)
    k = intensity
    return Capture(
        brightness=1.0 + _spread(u[0], -0.16, 0.16) * k,
        contrast=1.0 + _spread(u[1], -0.20, 0.18) * k,
        warmth=_spread(u[2], -9, 10) * k,
        # Intake photographs are posed facing the camera, so head turn is
        # slight. It is still modelled, because it is the one nuisance that
        # alignment cannot undo: a turned head is genuinely narrower on camera,
        # and no similarity transform can distinguish that from a narrow face.
        yaw=_spread(u[3], -0.04, 0.04) * k,
        roll=_spread(u[4], -5.0, 5.0) * k,
        scale=1.0 + _spread(u[5], -0.06, 0.06) * k,
        shift_x=_spread(u[6], -0.035, 0.035) * k,
        shift_y=_spread(u[7], -0.030, 0.030) * k,
        blur=_spread(u[8], 0.0, 1.3) * k,
        noise=_spread(u[9], 0.0, 7.0) * k,
        backdrop=(
            int(_spread(u[10], 198, 240)),
            int(_spread(u[11], 198, 240)),
            int(_spread(u[12], 196, 238)),
        ),
    )


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

def _shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)  # type: ignore[return-value]


def _face_outline(cx: float, cy: float, lat: FaceLatent, S: int) -> list[tuple[float, float]]:
    """
    The head as a closed polygon.

    The skull is a half-ellipse; the jaw is a superellipse whose exponent is
    driven by `chin_round`, so a square jaw and a pointed chin are genuinely
    different shapes rather than the same shape at different scales. This is
    the single largest contributor to how distinguishable two synthetic faces
    are, which is why it is parameterised rather than fixed.
    """
    rx = lat.face_width * S / 2
    skull_ry = lat.face_height * S * 0.40
    jaw_len = lat.face_height * S * 0.46
    n = _spread(lat.chin_round, 1.7, 3.4)

    pts: list[tuple[float, float]] = []
    # Skull: left temple, over the crown, to the right temple.
    for i in range(49):
        th = math.pi + math.pi * (i / 48)
        pts.append((cx + rx * math.cos(th), cy + skull_ry * math.sin(th)))
    # Jaw: right temple down to the chin and back up to the left temple.
    for i in range(1, 49):
        s = i / 48
        half = rx * lat.jaw_taper ** (s * 0.9) * (1 - s ** n) ** (1 / n)
        pts.append((cx + half, cy + jaw_len * s))
    for i in range(47, 0, -1):
        s = i / 48
        half = rx * lat.jaw_taper ** (s * 0.9) * (1 - s ** n) ** (1 / n)
        pts.append((cx - half, cy + jaw_len * s))
    return pts


def _hair_silhouette(d: ImageDraw.ImageDraw, cx: float, cy: float,
                     lat: FaceLatent, S: int, col):
    """The full outer shape of the hair, drawn behind the face."""
    rx = lat.face_width * S / 2
    skull_ry = lat.face_height * S * 0.40
    top = cy - skull_ry
    style = lat.hair_style

    if style == 0:                           # close crop
        d.chord([cx - rx * 1.03, top - S * 0.015,
                 cx + rx * 1.03, top + skull_ry * 1.5], 180, 360, fill=col)
    elif style == 1:                         # side part
        d.chord([cx - rx * 1.05, top - S * 0.025,
                 cx + rx * 1.05, top + skull_ry * 1.6], 180, 360, fill=col)
    elif style == 2:                         # receding
        d.chord([cx - rx * 1.01, top + S * 0.005,
                 cx + rx * 1.01, top + skull_ry * 1.5], 180, 360, fill=col)
    elif style == 3:                         # shoulder length
        d.polygon([(cx - rx * 1.13, cy + skull_ry * 1.5),
                   (cx - rx * 1.13, top + skull_ry * 0.25),
                   (cx, top - S * 0.03),
                   (cx + rx * 1.13, top + skull_ry * 0.25),
                   (cx + rx * 1.13, cy + skull_ry * 1.5)], fill=col)
    elif style == 4:                         # volume
        d.ellipse([cx - rx * 1.24, top - S * 0.065,
                   cx + rx * 1.24, cy + skull_ry * 0.34], fill=col)
    else:                                    # headscarf / covered
        d.polygon([(cx - rx * 1.15, cy + skull_ry * 1.30),
                   (cx - rx * 1.15, top + S * 0.015),
                   (cx, top - S * 0.042),
                   (cx + rx * 1.15, top + S * 0.015),
                   (cx + rx * 1.15, cy + skull_ry * 1.30)], fill=col)


def _fringe_region(d: ImageDraw.ImageDraw, cx: float, cy: float,
                   lat: FaceLatent, S: int):
    """
    White wherever hair is allowed to cover the face.

    Compositing the hair layer through this region — rather than overpainting
    the forehead with skin — is what keeps the hairline a clean single edge.
    The earlier carve-out approach left a strip of hair stranded between two
    arcs, which read as a dark band across the eyes.
    """
    rx = lat.face_width * S / 2
    skull_ry = lat.face_height * S * 0.40
    top = cy - skull_ry
    style = lat.hair_style
    W = 255

    # Where the hairline sits, as a fraction of the way down the forehead.
    drop = {0: 0.62, 1: 0.70, 2: 0.30, 3: 0.60, 4: 0.55, 5: 0.78}[style]
    line_y = top + lat.hairline * S * drop + skull_ry * 0.18

    if style == 1:                           # swept to one side
        d.polygon([(cx - rx * 1.3, top - S),
                   (cx + rx * 1.3, top - S),
                   (cx + rx * 1.3, line_y + skull_ry * 0.16),
                   (cx - rx * 1.3, line_y - skull_ry * 0.10)], fill=W)
    elif style == 2:                         # receding, gentle widow's peak
        d.polygon([(cx - rx * 1.3, top - S),
                   (cx + rx * 1.3, top - S),
                   (cx + rx * 1.3, line_y),
                   (cx + rx * 0.42, line_y - skull_ry * 0.16),
                   (cx, line_y + skull_ry * 0.10),
                   (cx - rx * 0.42, line_y - skull_ry * 0.16),
                   (cx - rx * 1.3, line_y)], fill=W)
    else:
        d.rectangle([cx - rx * 1.4, top - S, cx + rx * 1.4, line_y], fill=W)


def draw_face(lat: FaceLatent, backdrop: tuple[int, int, int]) -> Image.Image:
    """Render the person's face, front-on and evenly lit, before any capture effects."""
    S = OUTPUT_SIZE * SUPERSAMPLE
    img = Image.new("RGB", (S, S), backdrop)
    d = ImageDraw.Draw(img)

    cx, cy = S * 0.5, S * 0.50
    rx = lat.face_width * S / 2
    skull_ry = lat.face_height * S * 0.40
    jaw_len = lat.face_height * S * 0.46
    skin = lat.skin

    # Neck and shoulders, so the head is not floating.
    d.polygon([(cx - rx * 0.30, cy + jaw_len * 0.55),
               (cx + rx * 0.30, cy + jaw_len * 0.55),
               (cx + rx * 0.34, S), (cx - rx * 0.34, S)], fill=_shade(skin, 0.86))
    d.ellipse([cx - rx * 1.45, cy + jaw_len * 1.02, cx + rx * 1.45, S * 1.5],
              fill=_shade(lat.hair, 0.55))

    er = lat.ear_size * S
    for side in (-1, 1):
        d.ellipse([cx + side * rx * 0.96 - er * 0.5, cy + skull_ry * 0.10,
                   cx + side * rx * 0.96 + er * 0.5, cy + skull_ry * 0.10 + er * 1.5],
                  fill=_shade(skin, 0.94))

    # Hair goes down once behind the face, then again through the fringe mask.
    hair_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _hair_silhouette(ImageDraw.Draw(hair_layer), cx, cy, lat, S, lat.hair + (255,))
    img.paste(hair_layer, (0, 0), hair_layer)

    d.polygon(_face_outline(cx, cy, lat, S), fill=skin)

    fringe = Image.new("L", (S, S), 0)
    _fringe_region(ImageDraw.Draw(fringe), cx, cy, lat, S)
    from PIL import ImageChops
    img.paste(hair_layer, (0, 0), ImageChops.multiply(hair_layer.getchannel("A"), fringe))

    eye_dx = lat.eye_spacing * S / 2
    eye_y = cy + skull_ry * 0.10
    ew = lat.eye_size * S
    eh = ew * 0.56
    nw = lat.nose_width * S
    nl = lat.nose_length * S
    nose_base = eye_y + nl
    mw = lat.mouth_width * S
    mf = lat.mouth_fullness * S
    my = nose_base + jaw_len * 0.28

    # --- facial hair, under the mouth so the lips stay visible ------------
    if lat.facial_hair > 0.05:
        beard = _shade(lat.hair, 0.92)
        if lat.facial_hair >= 0.45:
            cutoff = my - mf * 1.4
            lower = [p for p in _face_outline(cx, cy, lat, S) if p[1] >= cutoff]
            if len(lower) > 3:
                d.polygon(lower, fill=beard)
        d.ellipse([cx - mw * 0.50, my - mf * 2.0,
                   cx + mw * 0.50, my - mf * 0.6], fill=beard)

    # --- nose: a soft shadow down the bridge, plus nostrils ---------------
    d.polygon([(cx - nw * 0.12, eye_y + nl * 0.20),
               (cx + nw * 0.10, eye_y + nl * 0.20),
               (cx + nw * 0.30, nose_base),
               (cx - nw * 0.30, nose_base)], fill=_shade(skin, 0.965))
    d.ellipse([cx - nw * 0.34, nose_base - nw * 0.20,
               cx + nw * 0.34, nose_base + nw * 0.16], fill=_shade(skin, 0.945))
    for side in (-1, 1):
        d.ellipse([cx + side * nw * 0.26 - nw * 0.105, nose_base - nw * 0.055,
                   cx + side * nw * 0.26 + nw * 0.105, nose_base + nw * 0.085],
                  fill=_shade(skin, 0.66))

    # --- eyes and brows ---------------------------------------------------
    for side in (-1, 1):
        ex = cx + side * eye_dx
        ey = eye_y + side * lat.eye_tilt * ew * 0.5
        d.ellipse([ex - ew / 2, ey - eh / 2, ex + ew / 2, ey + eh / 2],
                  fill=(246, 244, 240))
        ir = eh * 0.86
        d.ellipse([ex - ir / 2, ey - ir / 2, ex + ir / 2, ey + ir / 2], fill=lat.iris)
        pr = ir * 0.46
        d.ellipse([ex - pr / 2, ey - pr / 2, ex + pr / 2, ey + pr / 2], fill=(18, 16, 16))
        d.arc([ex - ew / 2, ey - eh / 2, ex + ew / 2, ey + eh / 2],
              180, 360, fill=_shade(skin, 0.40), width=int(eh * 0.26) + 1)

        bx = lat.brow_thickness * S
        by = ey - lat.brow_height * S
        tilt = lat.brow_angle * ew
        d.polygon([(ex - ew * 0.62, by + side * tilt + bx * 0.5),
                   (ex + ew * 0.62, by - side * tilt + bx * 0.5),
                   (ex + ew * 0.62, by - side * tilt - bx * 0.5),
                   (ex - ew * 0.62, by + side * tilt - bx * 0.5)],
                  fill=_shade(lat.hair, 0.88))

    # --- mouth, drawn last so facial hair never covers it -----------------
    d.ellipse([cx - mw / 2, my - mf / 2, cx + mw / 2, my + mf / 2],
              fill=_shade((196, 118, 110), 0.92))
    d.line([(cx - mw * 0.46, my), (cx + mw * 0.46, my)],
           fill=_shade(skin, 0.52), width=int(mf * 0.16) + 1)

    return img.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)


# --------------------------------------------------------------------------
# Applying capture conditions
# --------------------------------------------------------------------------

def apply_capture(img: Image.Image, cap: Capture) -> Image.Image:
    """
    Turn an evenly-lit front-on render into one particular photograph of it.

    Geometry first (head turn, camera tilt, framing), then optics (focus), then
    sensor response (exposure, white balance, noise). That ordering matters:
    blurring before rotating would smear along the wrong axis, and adding noise
    before blurring would let the blur average it away, producing a cleaner
    image than any real camera would deliver.
    """
    import numpy as np

    W, H = img.size
    cx, cy = W / 2.0, H / 2.0

    # One affine covering yaw squeeze, scale, roll and framing shift. Pillow
    # wants the output->input mapping, so this is the inverse of the transform
    # being described.
    th = math.radians(cap.roll)
    sx = cap.scale * (1.0 - abs(cap.yaw))     # a turned head is narrower on camera
    sy = cap.scale
    tx, ty = cap.shift_x * W, cap.shift_y * H

    a = math.cos(th) / sx
    b = math.sin(th) / sx
    dd = -math.sin(th) / sy
    e = math.cos(th) / sy
    c = cx - a * (cx + tx) - b * (cy + ty)
    f = cy - dd * (cx + tx) - e * (cy + ty)

    img = img.transform((W, H), Image.AFFINE, (a, b, c, dd, e, f),
                        resample=Image.BICUBIC, fillcolor=cap.backdrop)

    if cap.blur > 0.05:
        img = img.filter(ImageFilter.GaussianBlur(radius=cap.blur))

    arr = np.asarray(img, dtype=np.float32)

    # Exposure and contrast about mid-grey, then white balance.
    arr = (arr - 128.0) * cap.contrast + 128.0
    arr *= cap.brightness
    arr[..., 0] += cap.warmth
    arr[..., 2] -= cap.warmth

    if cap.noise > 0.2:
        rng = np.random.default_rng(abs(hash((cap.roll, cap.noise))) % (2**32))
        arr += rng.normal(0.0, cap.noise, arr.shape)

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def photograph(person_id: str, sex: str, birth_year: int, registry: str,
               intensity: float = 1.0) -> Image.Image:
    """One registry's photograph of one synthetic person."""
    lat = latent_for(person_id, sex, birth_year)
    cap = capture_for(person_id, registry, intensity)
    return apply_capture(draw_face(lat, cap.backdrop), cap)
