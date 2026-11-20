"""
Where is the model on familiar ground? (Meyer & Pebesma 2021, Methods in
Ecology and Evolution 12:1620, "area of applicability".)

For each new pixel: how far is it, in feature space, from the nearest
training point? Features are standardised and weighted by how much the model
relies on each. Distances are divided by the average distance between
training points (the "dissimilarity index", DI).

The cut-off comes from the training data itself: each training point's DI to
the nearest point in OTHER cross-validation folds. A new pixel beyond the
usual range of that (upper whisker: 75th percentile + 1.5 x IQR) is "unlike
anything the model learned from", and the map greys it out.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial.distance import pdist
from sklearn.neighbors import KDTree


class Applicability:
    def __init__(self, mean, sd, weights, train_z, d_mean, threshold):
        self.mean, self.sd, self.weights = map(np.asarray, (mean, sd, weights))
        self.train_z = np.asarray(train_z)
        self.d_mean, self.threshold = float(d_mean), float(threshold)
        self._tree = KDTree(self.train_z)

    @classmethod
    def fit(cls, X: np.ndarray, weights: np.ndarray, folds: np.ndarray, seed: int = 0):
        mean, sd = X.mean(axis=0), X.std(axis=0)
        sd[sd == 0] = 1.0
        w = np.asarray(weights) / np.sum(weights)
        z = (X - mean) / sd * w
        rng = np.random.default_rng(seed)
        sample = z[rng.choice(len(z), min(2000, len(z)), replace=False)]
        d_mean = pdist(sample).mean()
        di = np.empty(len(z))
        for f in np.unique(folds):
            inside, outside = folds == f, folds != f
            d, _ = KDTree(z[outside]).query(z[inside], k=1)
            di[inside] = d[:, 0] / d_mean
        q1, q3 = np.percentile(di, [25, 75])
        threshold = min(q3 + 1.5 * (q3 - q1), di.max())
        return cls(mean, sd, w, z, d_mean, threshold)

    def di(self, X: np.ndarray) -> np.ndarray:
        z = (X - self.mean) / self.sd * self.weights
        d, _ = self._tree.query(z, k=1)
        return d[:, 0] / self.d_mean

    def inside(self, X: np.ndarray) -> np.ndarray:
        return self.di(X) <= self.threshold

    def to_dict(self) -> dict:
        return {"mean": self.mean, "sd": self.sd, "weights": self.weights, "train_z": self.train_z,
                "d_mean": self.d_mean, "threshold": self.threshold}

    @classmethod
    def from_dict(cls, d: dict):
        return cls(**d)
