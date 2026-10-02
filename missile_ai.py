"""
missile_ai.py -- an on-device learner for the INBOUND MISSILE warning.

Everything here runs locally, in plain numpy (no PyTorch / TensorFlow):

  * While monitoring, it finds red, phrase-shaped text bands on screen (the
    same candidate rules the existing detector uses) and keeps tiny 24x96
    silhouettes of them -- never full screenshots.
  * It labels those silhouettes automatically from the EXISTING detector's
    confident verdicts, plus your "FALSE ALARM" corrections (the only real
    ground truth in the loop).
  * When the game is NOT being monitored, it trains a tiny CNN on that data
    (plus synthetic examples built from the bundled templates).
  * The model starts in "shadow mode": it only watches and is scored against
    the live detector. It can influence alerts only after it has proven itself
    AND you switch AI ASSIST on. The existing detector is never modified or
    replaced -- the model can only ADD a detection.

Nothing is ever sent anywhere; all data lives in a `missile_ai` folder next to
the app.
"""

import os
import json
import math
import time
import threading
from collections import deque

import numpy as np
from PIL import Image, ImageFilter

IN_H, IN_W = 24, 96          # size of the silhouette the CNN looks at
SRC_AUTO, SRC_USER = 0, 2    # where a label came from


# ---------------------------------------------------------------------------
# A tiny CNN in numpy (conv-relu-pool x2, dense, dense)
# ---------------------------------------------------------------------------
def _im2col(x, k=3, pad=1):
    n, c, h, w = x.shape
    xp = np.pad(x, ((0, 0), (0, 0), (pad, pad), (pad, pad)))
    s0, s1, s2, s3 = xp.strides
    win = np.lib.stride_tricks.as_strided(
        xp, shape=(n, c, h, w, k, k), strides=(s0, s1, s2, s3, s2, s3), writeable=False)
    return win.transpose(0, 2, 3, 1, 4, 5).reshape(n * h * w, c * k * k)


def _col2im(dcols, shape, k=3, pad=1):
    n, c, h, w = shape
    dc = dcols.reshape(n, h, w, c, k, k)
    dxp = np.zeros((n, c, h + 2 * pad, w + 2 * pad), dtype=dcols.dtype)
    for i in range(k):
        for j in range(k):
            dxp[:, :, i:i + h, j:j + w] += dc[:, :, :, :, i, j].transpose(0, 3, 1, 2)
    return dxp[:, :, pad:pad + h, pad:pad + w]


def _maxpool(a):
    n, c, h, w = a.shape
    r = a.reshape(n, c, h // 2, 2, w // 2, 2)
    return r.max(axis=(3, 5)), r


def _maxpool_back(dout, r, out):
    mask = (r == out[:, :, :, None, :, None])
    cnt = mask.sum(axis=(3, 5), keepdims=True)
    d = mask * (dout[:, :, :, None, :, None] / cnt)
    n, c, h2, _, w2, _ = r.shape
    return d.reshape(n, c, h2 * 2, w2 * 2)


class TinyCNN:
    """1x24x96 silhouette -> probability that it is the INBOUND MISSILE text."""

    def __init__(self, seed=0, dtype=np.float32):
        self.dtype = dtype
        rng = np.random.default_rng(seed)

        def he(shape, fan_in):
            return (rng.standard_normal(shape) * math.sqrt(2.0 / fan_in)).astype(dtype)

        flat = 16 * (IN_H // 4) * (IN_W // 4)
        self.p = {
            "c1w": he((8, 1, 3, 3), 9), "c1b": np.zeros(8, dtype),
            "c2w": he((16, 8, 3, 3), 72), "c2b": np.zeros(16, dtype),
            "d1w": he((flat, 32), flat), "d1b": np.zeros(32, dtype),
            "d2w": he((32, 1), 32), "d2b": np.zeros(1, dtype),
        }

    def forward(self, x, keep=False):
        p = self.p
        x = x.astype(self.dtype, copy=False)
        n = x.shape[0]
        cols1 = _im2col(x)
        z1 = cols1 @ p["c1w"].reshape(8, -1).T + p["c1b"]
        a1 = np.maximum(z1, 0).reshape(n, IN_H, IN_W, 8).transpose(0, 3, 1, 2)
        pool1, r1 = _maxpool(a1)
        cols2 = _im2col(pool1)
        z2 = cols2 @ p["c2w"].reshape(16, -1).T + p["c2b"]
        a2 = np.maximum(z2, 0).reshape(n, IN_H // 2, IN_W // 2, 16).transpose(0, 3, 1, 2)
        pool2, r2 = _maxpool(a2)
        flat = pool2.reshape(n, -1)
        h_pre = flat @ p["d1w"] + p["d1b"]
        h = np.maximum(h_pre, 0)
        logit = (h @ p["d2w"] + p["d2b"]).reshape(n)
        cache = None
        if keep:
            cache = dict(cols1=cols1, z1=z1, r1=r1, pool1=pool1, cols2=cols2, z2=z2, r2=r2,
                         pool2=pool2, flat=flat, h_pre=h_pre, h=h, n=n)
        return logit, cache

    def backward(self, dlogit, c):
        p = self.p
        n = c["n"]
        g = {}
        dl = dlogit.reshape(n, 1)
        g["d2w"] = c["h"].T @ dl
        g["d2b"] = dl.sum(0)
        dh = (dl @ p["d2w"].T) * (c["h_pre"] > 0)
        g["d1w"] = c["flat"].T @ dh
        g["d1b"] = dh.sum(0)
        dpool2 = (dh @ p["d1w"].T).reshape(c["pool2"].shape)
        da2 = _maxpool_back(dpool2, c["r2"], c["pool2"])
        dz2 = da2.transpose(0, 2, 3, 1).reshape(-1, 16) * (c["z2"] > 0)
        g["c2w"] = (dz2.T @ c["cols2"]).reshape(p["c2w"].shape)
        g["c2b"] = dz2.sum(0)
        dcols2 = dz2 @ p["c2w"].reshape(16, -1)
        dpool1 = _col2im(dcols2, c["pool1"].shape)
        da1 = _maxpool_back(dpool1, c["r1"], c["pool1"])
        dz1 = da1.transpose(0, 2, 3, 1).reshape(-1, 8) * (c["z1"] > 0)
        g["c1w"] = (dz1.T @ c["cols1"]).reshape(p["c1w"].shape)
        g["c1b"] = dz1.sum(0)
        return g

    def predict(self, X):
        """X: uint8 (N, 24, 96) -> probabilities (N,)"""
        X = np.asarray(X)
        out = []
        for i in range(0, len(X), 256):
            xb = (X[i:i + 256].astype(self.dtype) / 255.0)[:, None, :, :]
            logit, _ = self.forward(xb)
            out.append(1.0 / (1.0 + np.exp(-np.clip(logit, -30, 30))))
        return np.concatenate(out) if out else np.zeros(0, self.dtype)

    def get_params(self):
        return {k: v.copy() for k, v in self.p.items()}

    def set_params(self, params):
        self.p = {k: v.astype(self.dtype).copy() for k, v in params.items()}

    def save(self, path, **extra):
        tmp = path + ".tmp.npz"
        np.savez(tmp, arch=np.array(1), **self.p, **{k: np.array(v) for k, v in extra.items()})
        os.replace(tmp, path)

    @classmethod
    def load(cls, path):
        m = cls()
        with np.load(path, allow_pickle=False) as z:
            for k in m.p:
                if k not in z or z[k].shape != m.p[k].shape:
                    raise ValueError("model file doesn't match this architecture")
                m.p[k] = z[k].astype(np.float32)
        return m


class Adam:
    def __init__(self, params, lr=2e-3, b1=0.9, b2=0.999, eps=1e-8, wd=1e-4):
        self.lr, self.b1, self.b2, self.eps, self.wd = lr, b1, b2, eps, wd
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}
        self.t = 0

    def step(self, params, grads):
        self.t += 1
        for k in params:
            g = grads[k]
            if k.endswith("w"):
                g = g + self.wd * params[k]
            self.m[k] = self.b1 * self.m[k] + (1 - self.b1) * g
            self.v[k] = self.b2 * self.v[k] + (1 - self.b2) * g * g
            mh = self.m[k] / (1 - self.b1 ** self.t)
            vh = self.v[k] / (1 - self.b2 ** self.t)
            params[k] -= (self.lr * mh / (np.sqrt(vh) + self.eps)).astype(params[k].dtype)


def weighted_bce(logit, y, w):
    """Returns (mean weighted loss, dLoss/dlogit) for a batch."""
    z = logit.astype(np.float64)
    loss = np.maximum(z, 0) - z * y + np.log1p(np.exp(-np.abs(z)))
    sw = max(float(w.sum()), 1e-9)
    sig = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
    return float((w * loss).sum() / sw), (w * (sig - y) / sw)


# ---------------------------------------------------------------------------
# Input preparation, augmentation, synthetic bootstrap data
# ---------------------------------------------------------------------------
def to_input(glyph):
    """PIL 'L' silhouette -> uint8 (24, 96), the same way for every source."""
    box = glyph.getbbox()
    if box:
        glyph = glyph.crop(box)
    return np.asarray(glyph.resize((IN_W, IN_H), Image.BILINEAR), dtype=np.uint8)


def augment(X, rng):
    """uint8 (N,24,96) -> float32 (N,1,24,96): small shifts, stroke-thickness
    changes and speckle, so the model doesn't memorize exact pixels."""
    n = len(X)
    out = np.zeros((n, IN_H, IN_W), np.float32)
    for i in range(n):
        a = X[i].astype(np.float32) / 255.0
        dx, dy = int(rng.integers(-3, 4)), int(rng.integers(-1, 2))
        ap = np.pad(a, ((1, 1), (3, 3)))
        a = ap[1 - dy:1 - dy + IN_H, 3 - dx:3 - dx + IN_W]
        u = rng.random()
        if u < 0.25:      # thicker strokes
            a = np.maximum.reduce([a, np.roll(a, 1, 0), np.roll(a, 1, 1)])
        elif u < 0.40:    # thinner strokes
            a = np.minimum.reduce([a, np.roll(a, 1, 0), np.roll(a, 1, 1)])
        if rng.random() < 0.5:   # speckle
            flip = rng.random(a.shape) < rng.uniform(0.002, 0.02)
            a = np.where(flip, 1.0 - a, a)
        out[i] = a
    return out[:, None, :, :]


def _finish(art, rng):
    """256x40 (or any) uint8 art -> 24x96 sample, mimicking the real pipeline:
    real crops are small and get *up*scaled, so shrink first, re-binarize."""
    img = Image.fromarray(art.astype(np.uint8))
    box = img.getbbox()
    if not box:
        return np.zeros((IN_H, IN_W), np.uint8)
    img = img.crop(box)
    nw = int(rng.integers(70, 320))
    nh = max(7, int(nw / max(1.5, img.width / max(1, img.height)) * rng.uniform(0.9, 1.15)))
    img = img.resize((nw, nh), Image.BILINEAR).point(lambda v: 255 if v > 100 else 0)
    u = rng.random()
    if u < 0.25:
        img = img.filter(ImageFilter.MaxFilter(3))
    elif u < 0.40:
        img = img.filter(ImageFilter.MinFilter(3))
    return to_input(img)


def _textlike(rng):
    a = np.zeros((40, 256), np.uint8)
    x = int(rng.integers(0, 6))
    while x < 240:
        for _ in range(int(rng.integers(2, 9))):
            lw, gap = int(rng.integers(6, 16)), int(rng.integers(2, 6))
            top, bot = int(rng.integers(0, 12)), int(40 - rng.integers(0, 12))
            if x + lw >= 256:
                break
            style = int(rng.integers(0, 3))
            if style == 0:
                a[top:bot, x:x + 3] = 255
                a[top:bot, x + lw - 3:x + lw] = 255
                a[top:top + 3, x:x + lw] = 255
            elif style == 1:
                a[top:bot, x:x + lw] = 255
                a[top + 6:bot - 6, x + 3:x + lw - 3] = 0
            else:
                mid = (top + bot) // 2
                a[top:top + 3, x:x + lw] = 255
                a[mid:mid + 3, x:x + lw] = 255
                a[bot - 3:bot, x:x + lw] = 255
                a[top:bot, x:x + 3] = 255
            x += lw + gap
        x += int(rng.integers(8, 20))
    return a


def make_synthetic(phrase_tpl, rng, n_pos, n_neg):
    """Bootstrap data from the bundled phrase silhouette (float 40x256, 0..1).
    Positives: the phrase with realistic jitter. Negatives: the phrase's own
    strokes rearranged (same font, wrong layout), fragments, and text-like
    noise -- so the model must learn LAYOUT, not just 'red glyph strokes'."""
    base = (phrase_tpl > 0.5).astype(np.uint8) * 255
    X, y = [], []
    for _ in range(n_pos):
        h, w = base.shape
        l, r = int(w * rng.uniform(0, 0.02)), w - int(w * rng.uniform(0, 0.06))
        t, b = int(h * rng.uniform(0, 0.06)), h - int(h * rng.uniform(0, 0.06))
        X.append(_finish(base[t:b, l:r], rng))
        y.append(1)
    for _ in range(n_neg):
        kind = int(rng.integers(0, 5))
        if kind == 0:      # slices shuffled
            k = int(rng.integers(3, 9))
            cuts = np.sort(rng.choice(np.arange(8, base.shape[1] - 8), k - 1, replace=False))
            parts = np.split(base, cuts, axis=1)
            order = rng.permutation(len(parts))
            if (order == np.arange(len(parts))).all():
                order = order[::-1]
            art = np.concatenate([parts[i] for i in order], axis=1)
        elif kind == 1:    # mirrored
            art = base[:, ::-1]
        elif kind == 2:    # a fragment stretched to full width
            span = rng.uniform(0.35, 0.7)
            s = rng.uniform(0, 1 - span)
            art = base[:, int(s * base.shape[1]):int((s + span) * base.shape[1])]
        elif kind == 3:    # upside-down
            art = base[::-1, :]
        else:              # generic text-like noise
            art = _textlike(rng)
        X.append(_finish(art, rng))
        y.append(0)
    return np.array(X, np.uint8), np.array(y, np.int8)


# ---------------------------------------------------------------------------
# Finding candidates on screen (same rules as the existing detector)
# ---------------------------------------------------------------------------
def red_mask(arr):
    """Same red-text rule the detector uses, but cheaper: cut down to pixels
    with a strong red channel first, then apply the exact same tests to just
    those pixels (identical result, a fraction of the work on a full frame)."""
    height, width = arr.shape[:2]
    flat = arr[:, :, :3].reshape(-1, 3)
    idx = np.flatnonzero(flat[:, 0] >= 85)
    mask = np.zeros(height * width, bool)
    if len(idx):
        px = flat[idx].astype(np.int16)
        r, g, b = px[:, 0], px[:, 1], px[:, 2]
        ok = ((r - g) >= 20) & ((r - b) >= 18) & (r >= g * 1.10)
        mask[idx[ok]] = True
    return mask.reshape(height, width)


PREFIXES = (0.70, 0.75, 0.80, 0.85, 0.90)


def extract_candidates(img, runs_fn, teacher_fn, max_candidates=12):
    """Red phrase-sized bands -> [{'glyph': PIL 'L', 'teacher': score, 'box': ..}].
    'teacher' is the EXISTING detector's shape score for the best-aligned
    variant of that band; that same best variant is what the CNN looks at."""
    arr = np.asarray(img)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return []
    mask = red_mask(arr)
    height, width = mask.shape
    scale = max(0.75, width / 2048.0)
    out = []
    row_runs = runs_fn(mask.sum(axis=1) >= max(3, int(3 * scale)), allowed_gap=max(1, int(2 * scale)))
    for y1, y2 in row_runs:
        band_h = y2 - y1
        if band_h < max(3, int(5 * scale)) or band_h > int(90 * scale):
            continue
        py1, py2 = max(0, y1 - 2), min(height, y2 + 2)
        stripe = mask[py1:py2]
        for x1, x2 in runs_fn(stripe.any(axis=0), allowed_gap=max(5, int(10 * scale))):
            if not (int(55 * scale) <= x2 - x1 <= int(420 * scale)):
                continue
            px1, px2 = max(0, x1 - 3), min(width, x2 + 3)
            cand = Image.fromarray(np.where(mask[py1:py2, px1:px2], 255, 0).astype(np.uint8))
            box = cand.getbbox()
            glyphs = cand.crop(box) if box else cand
            if glyphs.width / max(1, glyphs.height) < 3.5:
                continue
            variants = [glyphs] + [glyphs.crop((0, 0, max(1, int(glyphs.width * f)), glyphs.height))
                                   for f in PREFIXES]
            scores = [teacher_fn(v) for v in variants]
            k = int(np.argmax(scores))
            out.append({"glyph": variants[k], "teacher": float(scores[k]), "box": (px1, py1, px2, py2)})
            if len(out) >= max_candidates:
                return out
    return out


# ---------------------------------------------------------------------------
# The stored dataset (only tiny silhouettes + labels)
# ---------------------------------------------------------------------------
def _lowres(X):
    return X.reshape(-1, 6, 4, 24, 4).mean(axis=(2, 4)).reshape(len(X), -1) / 255.0


class Dataset:
    CAP = 8000

    def __init__(self, path):
        self.path = path
        self.X = np.zeros((self.CAP, IN_H, IN_W), np.uint8)
        self.F = np.zeros((self.CAP, 144), np.float32)
        self.y = np.zeros(self.CAP, np.int8)
        self.src = np.zeros(self.CAP, np.int8)
        self.teacher = np.zeros(self.CAP, np.float32)
        self.uid = np.zeros(self.CAP, np.int64)
        self.n = 0
        self.next_uid = 1
        self.dirty = False

    def _near(self, f, label, thresh=0.03, window=600):
        idx = np.flatnonzero(self.y[:self.n] == label)[-window:]
        if not len(idx):
            return None
        d = np.abs(self.F[idx] - f).mean(axis=1)
        k = int(d.argmin())
        return int(idx[k]) if d[k] < thresh else None

    def add(self, x, label, teacher, src):
        """Returns the new uid, or None if it's a near-duplicate of something
        already stored (a warning sitting on screen for 10s is ONE sample)."""
        f = _lowres(x[None])[0]
        if self._near(f, label) is not None:
            return None
        if src == SRC_AUTO:
            # The detector's opinion never overrides yours: if you already
            # corrected this same picture the other way, leave it alone.
            j = self._near(f, 1 - label)
            if j is not None and self.src[j] == SRC_USER:
                return None
        if src == SRC_USER:
            # A user correction beats any conflicting auto label for the same picture.
            j = self._near(f, 1 - label)
            if j is not None:
                self.y[j], self.src[j] = label, SRC_USER
                self.dirty = True
                return int(self.uid[j])
        if self.n >= self.CAP:
            self._evict()
        i = self.n
        self.X[i], self.F[i], self.y[i] = x, f, label
        self.src[i], self.teacher[i] = src, teacher
        self.uid[i] = self.next_uid
        self.next_uid += 1
        self.n += 1
        self.dirty = True
        return int(self.uid[i])

    def _evict(self):
        n, drop = self.n, max(1, self.CAP // 10)
        idx = np.array([], dtype=int)
        for want_label in (0, 1):
            idx = np.flatnonzero((self.src[:n] == SRC_AUTO) & (self.y[:n] == want_label))[:drop]
            if len(idx) >= drop // 2:
                break
        if not len(idx):          # nothing but your own corrections stored: drop the oldest
            idx = np.arange(min(drop, n))
        keep = np.ones(n, bool)
        keep[idx] = False
        self._compact(keep)

    def _compact(self, keep):
        m = int(keep.sum())
        for arr in (self.X, self.F, self.y, self.src, self.teacher, self.uid):
            arr[:m] = arr[:self.n][keep]
        self.n = m
        self.dirty = True

    def relabel(self, uids, label, src):
        if not len(uids):
            return 0
        hit = np.isin(self.uid[:self.n], np.array(uids, np.int64))
        self.y[:self.n][hit] = label
        self.src[:self.n][hit] = src
        self.dirty = self.dirty or bool(hit.any())
        return int(hit.sum())

    def counts(self):
        y, s = self.y[:self.n], self.src[:self.n]
        return {"n": self.n, "pos": int((y == 1).sum()), "neg": int((y == 0).sum()),
                "user": int((s == SRC_USER).sum())}

    def snapshot(self):
        n = self.n
        return (self.X[:n].copy(), self.y[:n].copy(), self.src[:n].copy(), self.uid[:n].copy())

    def save(self):
        n = self.n
        tmp = self.path + ".tmp.npz"
        np.savez_compressed(tmp, X=self.X[:n], y=self.y[:n], src=self.src[:n],
                            teacher=self.teacher[:n], uid=self.uid[:n], next_uid=np.array(self.next_uid))
        os.replace(tmp, self.path)
        self.dirty = False

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with np.load(self.path, allow_pickle=False) as z:
                n = min(len(z["y"]), self.CAP)
                self.X[:n], self.y[:n], self.src[:n] = z["X"][:n], z["y"][:n], z["src"][:n]
                self.teacher[:n], self.uid[:n] = z["teacher"][:n], z["uid"][:n]
                self.next_uid = int(z["next_uid"])
                self.n = n
                self.F[:n] = _lowres(self.X[:n]) if n else 0
        except Exception:
            self.n = 0  # a damaged file just means starting fresh


def _lower_thread_priority():
    """Best effort: run this thread below the game/alert threads (Windows)."""
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetThreadPriority(k.GetCurrentThread(), -1)   # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass


# ---------------------------------------------------------------------------
# The learner
# ---------------------------------------------------------------------------
class MissileLearner:
    ALERT_THR = 0.95     # model probability that counts as "it sees the warning"
    TEACHER_HIT = 0.48   # the detector's own trigger level for the phrase score
    AUTO_POS = 0.62      # confident enough to label a sample "warning"
    AUTO_NEG = 0.25      # clearly-not enough to label it "not the warning"

    def __init__(self, data_dir, runs_fn, teacher_fn, template_loader, on_ai_hit=None, log=None):
        os.makedirs(data_dir, exist_ok=True)
        self.dir = data_dir
        self.runs_fn, self.teacher_fn, self.template_loader = runs_fn, teacher_fn, template_loader
        self.on_ai_hit = on_ai_hit
        self.log = log or (lambda *a: None)
        self.lock = threading.RLock()
        self.dataset = Dataset(os.path.join(data_dir, "dataset.npz"))
        self.dataset.load()
        self.model_path = os.path.join(data_dir, "model.npz")
        self.meta_path = os.path.join(data_dir, "meta.json")
        self.meta = {"version": 0, "trained_count": 0, "feedback": 0, "feedback_trained": 0,
                     "val": {}, "stats": self._blank_stats()}
        self.model = None
        self._load_state()
        self._busy = threading.Event()        # set while monitoring -> training waits
        self._stop = threading.Event()
        self._abort_train = threading.Event()
        self._collect_thread = self._train_thread = None
        self.training_progress = None
        # The most recent "episode" of detector hits (hits <5s apart belong to one
        # episode). FALSE ALARM corrects exactly that episode, however long ago.
        self._ep_hits = []                    # [(x, teacher)]
        self._ep_last = 0.0
        self._last_teacher_flag = 0.0
        self._last_save = time.time()
        self.assist_active = False

    # -- persistence -----------------------------------------------------
    @staticmethod
    def _blank_stats():
        return {"frames": 0, "teacher_pos": 0, "both": 0, "model_only": 0, "events": 0}

    def _load_state(self):
        try:
            with open(self.meta_path) as f:
                m = json.load(f)
            self.meta.update(m)
            self.meta["stats"] = {**self._blank_stats(), **m.get("stats", {})}
        except Exception:
            pass
        if os.path.exists(self.model_path):
            try:
                self.model = TinyCNN.load(self.model_path)
            except Exception:
                self.model = None

    def _save_meta(self):
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.meta, f)
        os.replace(tmp, self.meta_path)

    def flush(self, force=False):
        """Write to disk at most about once a minute (or when forced)."""
        with self.lock:
            if force or time.time() - self._last_save > 60:
                if self.dataset.dirty:
                    self.dataset.save()
                self._save_meta()
                self._last_save = time.time()

    # -- collecting ----------------------------------------------------------
    def process_frame(self, img, real_confirmed=None):
        """One screen frame: find candidates, learn from the confident ones,
        and score the model in shadow mode. Returns (model_prob|None,
        best_detector_score, n_candidates).

        real_confirmed, when given, is the REAL detector's own confirmed
        verdict for this same instant (it runs its own capture at a much
        higher rate with its own hit/miss confirmation logic). This collector
        has its own, separately-timed, lower-rate capture purely for gathering
        training data -- a transient warning can easily land outside THIS
        collector's own sampling window even while the real detector, at a
        higher rate, correctly caught and confirmed it. Without this, a user
        could see many genuinely confirmed warnings while this collector's
        own shadow-tracking stats never move, so readiness would never be
        reached no matter how well the real detector is working. real_confirmed
        is combined with this collector's own signal (OR, not an override), so
        a case where THIS capture spots something a beat before the real
        detector's own confirmation delay catches up still counts too."""
        cands = extract_candidates(img, self.runs_fn, self.teacher_fn)
        model, now = self.model, time.time()
        frame_prob, teacher_max, stored = None, 0.0, 0
        for c in cands:
            x = to_input(c["glyph"])
            teacher_max = max(teacher_max, c["teacher"])
            if model is not None:
                p = float(model.predict(x[None])[0])
                frame_prob = p if frame_prob is None else max(frame_prob, p)
            if c["teacher"] >= self.TEACHER_HIT:
                with self.lock:
                    if now - self._ep_last > 5.0:
                        self._ep_hits = []            # a new episode begins
                    self._ep_last = now
                    if len(self._ep_hits) < 30:
                        self._ep_hits.append((x, c["teacher"]))
            label = 1 if c["teacher"] >= self.AUTO_POS else (0 if c["teacher"] <= self.AUTO_NEG else None)
            if label is not None and stored < 3:
                with self.lock:
                    if self.dataset.add(x, label, c["teacher"], SRC_AUTO) is not None:
                        stored += 1

        teacher_flag = bool(real_confirmed) or (teacher_max >= self.TEACHER_HIT)
        # Score this frame whenever we have real ground truth for it (even if
        # this collector's own capture found no candidates at all), or -- for
        # standalone/backward-compatible use with no real_confirmed wired in
        # -- only when it found something itself, same as before.
        should_score = model is not None and (cands or real_confirmed is not None)
        if should_score:
            self._update_shadow(teacher_flag, (frame_prob or 0.0) >= self.ALERT_THR, now)
        return frame_prob, teacher_max, len(cands)

    def _update_shadow(self, teacher_flag, model_flag, now):
        s = self.meta["stats"]
        s["frames"] += 1
        if teacher_flag:
            s["teacher_pos"] += 1
            s["both"] += int(model_flag)
            if now - self._last_teacher_flag > 5.0:
                s["events"] += 1
            self._last_teacher_flag = now
        elif model_flag:
            s["model_only"] += 1

    def is_ready(self):
        """Ready = it has been scored against the live detector on enough real
        warnings, catching nearly all of them without inventing its own."""
        if self.model is None:
            return False
        s = self.meta["stats"]
        if s["frames"] < 300 or s["events"] < 3 or s["teacher_pos"] < 30:
            return False
        return s["both"] / s["teacher_pos"] >= 0.9 and s["model_only"] / s["frames"] <= 0.02

    def start(self, grabber_factory, hz=3.0, assist_getter=None, teacher_getter=None):
        """teacher_getter, if given, is a zero-arg callable returning the REAL
        detector's current confirmed state (e.g. the App's own
        self.state["missile"]) -- see process_frame() for why this matters."""
        if self._collect_thread and self._collect_thread.is_alive():
            return
        self._stop.clear()
        self._busy.set()
        self._collect_thread = threading.Thread(
            target=self._collect_loop, args=(grabber_factory, hz, assist_getter, teacher_getter), daemon=True)
        self._collect_thread.start()

    def _set_assist(self, value):
        if value != self.assist_active:
            self.assist_active = value
            if self.on_ai_hit:
                self.on_ai_hit(value)

    def _collect_loop(self, grabber_factory, hz, assist_getter, teacher_getter=None):
        _lower_thread_priority()
        interval = 1.0 / max(0.5, hz)
        hits = misses = 0
        try:
            with grabber_factory() as grab:
                while not self._stop.is_set():
                    t0 = time.perf_counter()
                    try:
                        real_confirmed = teacher_getter() if teacher_getter else None
                        prob, _t, _n = self.process_frame(grab.grab(), real_confirmed=real_confirmed)
                    except Exception as e:
                        self.log("Missile AI frame error:", e)
                        prob = None
                    assist_on = bool(assist_getter and assist_getter()) and self.is_ready()
                    if assist_on and prob is not None and prob >= self.ALERT_THR:
                        hits, misses = hits + 1, 0
                        if hits >= 2:
                            self._set_assist(True)
                    else:
                        hits, misses = 0, misses + 1
                        if misses >= 3 or not assist_on:
                            self._set_assist(False)
                    self.flush()
                    worked = time.perf_counter() - t0
                    # never use more than ~1/3 of a core, however slow a frame is
                    rest = max(interval - worked, 2.0 * worked)
                    self._stop.wait(rest)
        except Exception as e:
            self.log("Missile AI stopped:", e)
        finally:
            self._set_assist(False)

    def stop(self):
        self._stop.set()
        t = self._collect_thread
        if t is not None:
            t.join(timeout=3)
        self._collect_thread = None
        self._busy.clear()
        self.flush(force=True)

    def set_busy(self, busy):
        """True while the game is being monitored: training waits, so it can
        never compete with the alerts for CPU."""
        (self._busy.set if busy else self._busy.clear)()

    def shutdown(self):
        self._abort_train.set()
        self.stop()

    # -- your corrections ---------------------------------------------------
    def false_alarm(self):
        """'That popup was NOT a missile': mark everything the detector flagged
        in its most recent episode as NOT the warning -- however long ago that
        was, since you can only press this after alt-tabbing out of the game.
        This is the one real ground-truth signal the learner gets."""
        n = 0
        with self.lock:
            for x, teacher in self._ep_hits:
                if self.dataset.add(x, 0, teacher, SRC_USER) is not None:
                    n += 1
            self._ep_hits = []
            self.meta["feedback"] += n
        self.flush(force=True)
        return n

    # -- training ------------------------------------------------------------
    def _should_train(self):
        if self.model is None:
            return True
        # next_uid rises with every sample ever stored, so this keeps working after
        # the dataset is full and old samples are being evicted (n stops growing then)
        new = self.dataset.next_uid - self.meta.get("trained_uid", 0)
        return new >= 40 or self.meta["feedback"] > self.meta["feedback_trained"]

    def request_training(self, force=False):
        if self._train_thread and self._train_thread.is_alive():
            return False
        if not force and not self._should_train():
            return False
        self._abort_train.clear()
        self._train_thread = threading.Thread(target=self._train_worker, daemon=True)
        self._train_thread.start()
        return True

    def _train_worker(self):
        _lower_thread_priority()
        try:
            res = self.train()
            self.log("Missile AI training:", res)
        except Exception as e:
            self.log("Missile AI training error:", e)
        finally:
            self.training_progress = None

    def _templates(self):
        try:
            return np.asarray(self.template_loader("missile_template.png"), np.float32)
        except Exception:
            return None

    def train(self, epochs=24, max_seconds=150, seed=None):
        rng = np.random.default_rng(seed)
        tpl = self._templates()
        if tpl is None or tpl.shape != (40, 256):
            return {"ok": False, "reason": "phrase template unavailable"}
        with self.lock:
            X, y, src, uid = self.dataset.snapshot()
            fb = self.meta["feedback"]
            trained_uid = self.dataset.next_uid
        is_val = (uid % 5 == 0)
        Xr, yr, sr = X[~is_val], y[~is_val], src[~is_val]
        Xv, yv, sv = X[is_val], y[is_val], src[is_val]
        # keep every positive and correction; sample the (plentiful) negatives
        neg = np.flatnonzero((yr == 0) & (sr == SRC_AUTO))
        if len(neg) > 4000:
            drop = rng.choice(neg, len(neg) - 4000, replace=False)
            keep = np.ones(len(yr), bool)
            keep[drop] = False
            Xr, yr, sr = Xr[keep], yr[keep], sr[keep]
        real_pos = int((yr == 1).sum())
        n_pos = int(np.clip(400 - 2 * real_pos, 120, 400))
        Xs, ys = make_synthetic(tpl, rng, n_pos, 500)
        perm = rng.permutation(len(Xs))
        cut = int(len(Xs) * 0.85)
        Xs_tr, ys_tr, Xs_va, ys_va = Xs[perm[:cut]], ys[perm[:cut]], Xs[perm[cut:]], ys[perm[cut:]]

        def weights(ys_, srcs_, syn):
            w = np.where(srcs_ == SRC_USER, 3.0, 1.0).astype(np.float64)
            return w * (0.5 if syn else 1.0)

        Xtr = np.concatenate([Xr, Xs_tr])
        ytr = np.concatenate([yr, ys_tr]).astype(np.float64)
        wtr = np.concatenate([weights(yr, sr, False), weights(ys_tr, np.zeros(len(ys_tr)), True)])
        Xva = np.concatenate([Xv, Xs_va])
        yva = np.concatenate([yv, ys_va]).astype(np.float64)
        wva = np.concatenate([weights(yv, sv, False), weights(ys_va, np.zeros(len(ys_va)), True)])
        pw, nw = wtr[ytr == 1].sum(), wtr[ytr == 0].sum()
        if 0 < pw < nw:
            wtr = np.where(ytr == 1, wtr * min(6.0, nw / pw), wtr)
            wva = np.where(yva == 1, wva * min(6.0, nw / pw), wva)

        def val_loss(m):
            lg, _ = m.forward((Xva.astype(np.float32) / 255.0)[:, None])
            return weighted_bce(lg, yva, wva)[0]

        model = TinyCNN(seed=int(rng.integers(0, 1 << 30)))
        if self.model is not None:                       # continue from what it already knows
            model.set_params(self.model.get_params())
        opt = Adam(model.p)
        best, best_loss, patience, t0 = model.get_params(), val_loss(model), 0, time.time()
        for ep in range(epochs):
            order = rng.permutation(len(Xtr))
            for i in range(0, len(order), 64):
                while self._busy.is_set() and not self._abort_train.is_set():
                    time.sleep(0.5)                       # never compete with a live game session
                if self._abort_train.is_set():
                    break
                idx = order[i:i + 64]
                logit, cache = model.forward(augment(Xtr[idx], rng), keep=True)
                _, dz = weighted_bce(logit, ytr[idx], wtr[idx])
                opt.step(model.p, model.backward(dz, cache))
                time.sleep(0.001)
            self.training_progress = (ep + 1, epochs)
            vl = val_loss(model)
            if vl < best_loss - 1e-4:
                best, best_loss, patience = model.get_params(), vl, 0
            else:
                patience += 1
            if patience >= 4 or self._abort_train.is_set() or time.time() - t0 > max_seconds:
                break
        model.set_params(best)

        old_loss = val_loss(self.model) if self.model is not None else None
        real_val = len(Xv)
        pv = model.predict(Xv) if real_val else np.zeros(0)
        tp = int(((pv >= 0.5) & (yv == 1)).sum())
        fp = int(((pv >= 0.5) & (yv == 0)).sum())
        fn = int(((pv < 0.5) & (yv == 1)).sum())
        val = {"loss": round(best_loss, 4), "real_val": real_val,
               "precision": round(tp / max(1, tp + fp), 3), "recall": round(tp / max(1, tp + fn), 3)}
        if old_loss is not None and best_loss > old_loss * 1.10:
            return {"ok": False, "reason": "not better than current model", "val": val}
        with self.lock:
            self.model = model
            self.meta.update(version=self.meta["version"] + 1, trained_count=len(X),
                             trained_uid=trained_uid, feedback_trained=fb, val=val, stats=self._blank_stats())
            self._last_teacher_flag = 0.0
            model.save(self.model_path, version=self.meta["version"])
        self.flush(force=True)
        return {"ok": True, "version": self.meta["version"], "val": val,
                "seconds": round(time.time() - t0, 1)}

    # -- status for the UI -----------------------------------------------------
    def status(self):
        c = self.dataset.counts()
        s = self.meta["stats"]
        if self._train_thread and self._train_thread.is_alive():
            ep = self.training_progress
            text = f"training {ep[0]}/{ep[1]}" if ep else "training..."
            phase = "training"
        elif self.model is None:
            phase, text = "collecting", f"collecting - {c['n']} samples"
        elif self.is_ready():
            phase = "ready"
            text = f"v{self.meta['version']} ready - {s['both']}/{s['teacher_pos']} agree"
        else:
            phase = "checking"
            text = (f"v{self.meta['version']} testing - {s['events']}/3 warnings seen"
                    if s["events"] < 3 else f"v{self.meta['version']} testing...")
        return {"phase": phase, "text": text, "ready": self.is_ready(), "counts": c,
                "version": self.meta["version"], "stats": dict(s)}
