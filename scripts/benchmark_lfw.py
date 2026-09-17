"""
Measure the face matcher on real photographs.

    python scripts/benchmark_lfw.py --pairs 400

Everything else in this project runs on synthetic faces, which is right for the
demo and useless for answering "does the face matching actually work?" -- a
descriptor can look fine on drawn faces and fall apart on photographs of real
people under real lighting.

So this runs the standard benchmark instead. Labeled Faces in the Wild is the
canonical face-verification test set: pairs of photographs, half of them the
same person and half not, and the job is to tell which is which. The photographs
are of public figures and are used here only to score the matcher -- no record
in this project is ever attached to a real person's face.

Both backends are measured on identical pairs, because the question that
matters is not whether face matching can work but whether *ours* does.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def accuracy_at_best_threshold(scores: np.ndarray, same: np.ndarray):
    """Standard LFW protocol: the best single cosine threshold."""
    order = np.unique(scores)
    best_acc, best_t = 0.0, 0.0
    for t in order:
        acc = float(((scores >= t) == same).mean())
        if acc > best_acc:
            best_acc, best_t = acc, float(t)
    return best_acc, best_t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", type=int, default=400)
    args = ap.parse_args()

    from sklearn.datasets import fetch_lfw_pairs
    print("Loading LFW test pairs...")
    data = fetch_lfw_pairs(subset="test", color=True, resize=1.0, funneled=True)
    # LFW lists every same-person pair first and every different-person pair
    # after, so taking the first N gives a set with no negatives in it at all --
    # and an accuracy of 100% that means nothing. Sample both halves.
    labels = data.target.astype(bool)
    pos = np.flatnonzero(labels)
    neg = np.flatnonzero(~labels)
    take = max(1, args.pairs // 2)
    chosen = np.concatenate([pos[:take], neg[:take]])
    pairs = data.pairs[chosen]
    same = labels[chosen]
    print(f"  {len(pairs)} pairs — {int(same.sum())} same person, "
          f"{int((~same).sum())} different\n")

    def to_image(arr) -> Image.Image:
        return Image.fromarray((arr * 255).astype(np.uint8), "RGB")

    from engine.face_embedding import ArcFaceOnnxBackend, GradientDescriptorBackend

    backends = [("ours (gradient descriptor)", GradientDescriptorBackend())]
    model = ROOT / "models" / "arcface.onnx"
    if model.exists():
        backends.append(("ArcFace ResNet100", ArcFaceOnnxBackend(model)))
    else:
        print(f"No model at {model} — skipping ArcFace. See ARCFACE_SETUP.\n")

    results = []
    for name, backend in backends:
        started = time.time()
        scores = np.empty(len(pairs), dtype=np.float32)
        for i, (a, b) in enumerate(pairs):
            va = backend.generate(to_image(a))
            vb = backend.generate(to_image(b))
            scores[i] = float(va @ vb) / (
                float(np.linalg.norm(va)) * float(np.linalg.norm(vb)) + 1e-9)
        acc, threshold = accuracy_at_best_threshold(scores, same)
        elapsed = time.time() - started
        gap = float(scores[same].mean() - scores[~same].mean())
        results.append((name, acc, threshold, gap, elapsed))
        print(f"{name}")
        print(f"  accuracy        {acc * 100:5.1f}%   (at cosine >= {threshold:.3f})")
        print(f"  same person     {scores[same].mean():.3f} mean cosine")
        print(f"  different       {scores[~same].mean():.3f}")
        print(f"  separation      {gap:.3f}")
        print(f"  speed           {elapsed / (len(pairs) * 2) * 1000:.0f} ms per face\n")

    if len(results) == 2:
        (n1, a1, *_), (n2, a2, *_) = results
        print(f"On real photographs, {n2} is {(a2 - a1) * 100:+.1f} points "
              f"{'better' if a2 > a1 else 'worse'} than {n1}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
