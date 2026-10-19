"""Deep-learning building blocks (PyTorch, CPU-friendly): tabular autoencoder, MLP, sequence models.

All training loops are seeded and deterministic on CPU; they run with a bounded budget so that the
comparison with classical baselines is reproducible on modest hardware.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
from torch import nn

torch.set_num_threads(2)


def seed_all(seed: int) -> None:
    torch.manual_seed(seed)
    np.random.seed(seed)


class Standardizer:
    def __init__(self) -> None:
        self.mean: np.ndarray | None = None
        self.std: np.ndarray | None = None

    def fit(self, X: np.ndarray) -> Standardizer:
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0) + 1e-6
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        assert self.mean is not None and self.std is not None
        return np.clip((X - self.mean) / self.std, -10, 10).astype(np.float32)


class AutoEncoder(nn.Module):
    def __init__(self, dim: int, hidden: int = 32, bottleneck: int = 8) -> None:
        super().__init__()
        self.enc = nn.Sequential(nn.Linear(dim, hidden), nn.ReLU(), nn.Linear(hidden, bottleneck), nn.ReLU())
        self.dec = nn.Sequential(nn.Linear(bottleneck, hidden), nn.ReLU(), nn.Linear(hidden, dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dec(self.enc(x))


class MLP(nn.Module):
    def __init__(self, dim: int, hidden: tuple[int, ...] = (64, 32), dropout: float = 0.1) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        prev = dim
        for h in hidden:
            layers += [nn.Linear(prev, h), nn.ReLU(), nn.Dropout(dropout)]
            prev = h
        layers.append(nn.Linear(prev, 1))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


class LSTMClassifier(nn.Module):
    def __init__(self, dim: int, hidden: int = 48) -> None:
        super().__init__()
        self.lstm = nn.LSTM(dim, hidden, batch_first=True)
        self.head = nn.Sequential(nn.Linear(hidden, 32), nn.ReLU(), nn.Linear(32, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)


class TransformerClassifier(nn.Module):
    """Small causal-agnostic transformer encoder over the card's last transactions (TabFormer-like)."""

    def __init__(self, dim: int, d_model: int = 32, heads: int = 4, layers: int = 2, max_len: int = 64) -> None:
        super().__init__()
        self.proj = nn.Linear(dim, d_model)
        self.pos = nn.Parameter(torch.zeros(1, max_len, d_model))
        enc_layer = nn.TransformerEncoderLayer(d_model, heads, dim_feedforward=64, dropout=0.1, batch_first=True)
        self.encoder = nn.TransformerEncoder(enc_layer, layers)
        self.head = nn.Linear(d_model, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.proj(x) + self.pos[:, : x.shape[1], :]
        h = self.encoder(h)
        return self.head(h[:, -1, :]).squeeze(-1)


@dataclass
class FitResult:
    model: nn.Module
    epochs: int
    seconds: float
    history: list[float]


def fit_classifier(model: nn.Module, X: np.ndarray, y: np.ndarray, *, w: np.ndarray | None = None,
                   epochs: int = 5, batch: int = 512, lr: float = 1e-3, seed: int = 7,
                   pos_weight: float | None = None) -> FitResult:
    seed_all(seed)
    t0 = time.perf_counter()
    Xt = torch.from_numpy(X.astype(np.float32))
    yt = torch.from_numpy(y.astype(np.float32))
    wt = torch.from_numpy((w if w is not None else np.ones(len(y))).astype(np.float32))
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    pw = torch.tensor(pos_weight) if pos_weight else None
    loss_fn = nn.BCEWithLogitsLoss(reduction="none", pos_weight=pw)
    hist = []
    n = len(yt)
    gen = torch.Generator().manual_seed(seed)
    for _ in range(epochs):
        model.train()
        perm = torch.randperm(n, generator=gen)
        total = 0.0
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            opt.zero_grad()
            loss = (loss_fn(model(Xt[idx]), yt[idx]) * wt[idx]).mean()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            total += float(loss) * len(idx)
        hist.append(total / n)
    return FitResult(model, epochs, time.perf_counter() - t0, hist)


def fit_autoencoder(model: AutoEncoder, X: np.ndarray, *, epochs: int = 5, batch: int = 1024, lr: float = 1e-3,
                    seed: int = 7) -> FitResult:
    seed_all(seed)
    t0 = time.perf_counter()
    Xt = torch.from_numpy(X.astype(np.float32))
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    hist = []
    gen = torch.Generator().manual_seed(seed)
    for _ in range(epochs):
        perm = torch.randperm(len(Xt), generator=gen)
        total = 0.0
        for i in range(0, len(Xt), batch):
            xb = Xt[perm[i:i + batch]]
            opt.zero_grad()
            loss = ((model(xb) - xb) ** 2).mean()
            loss.backward()
            opt.step()
            total += float(loss) * len(xb)
        hist.append(total / len(Xt))
    return FitResult(model, epochs, time.perf_counter() - t0, hist)


@torch.no_grad()
def predict_proba(model: nn.Module, X: np.ndarray, batch: int = 8192) -> np.ndarray:
    model.eval()
    out = []
    for i in range(0, len(X), batch):
        out.append(torch.sigmoid(model(torch.from_numpy(X[i:i + batch].astype(np.float32)))).numpy())
    return np.concatenate(out) if out else np.array([])


@torch.no_grad()
def reconstruction_error(model: AutoEncoder, X: np.ndarray, batch: int = 16384) -> np.ndarray:
    model.eval()
    out = []
    for i in range(0, len(X), batch):
        xb = torch.from_numpy(X[i:i + batch].astype(np.float32))
        out.append(((model(xb) - xb) ** 2).mean(dim=1).numpy())
    return np.concatenate(out) if out else np.array([])


def build_sequences(values: np.ndarray, group_starts: np.ndarray, targets: np.ndarray, seq_len: int) -> np.ndarray:
    """values: (N, F) rows sorted by (group, time); group_starts[i] = index of the first row of row i's group.
    Returns (len(targets), seq_len, F) windows ending at each target row (left-padded with zeros)."""
    F = values.shape[1]
    out = np.zeros((len(targets), seq_len, F), dtype=np.float32)
    for j, t in enumerate(targets):
        start = max(group_starts[t], t - seq_len + 1)
        window = values[start:t + 1]
        out[j, seq_len - len(window):] = window
    return out


def describe(model: nn.Module) -> dict[str, Any]:
    n_params = sum(p.numel() for p in model.parameters())
    return {"class": type(model).__name__, "parameters": int(n_params)}


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-x))


_ = math
