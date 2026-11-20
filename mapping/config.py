"""Read a plant's config file. Paths inside it are relative to the repo root."""

from __future__ import annotations

import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, "config", "prosopis.yaml")


def load_config(path: str | None = None) -> dict:
    with open(path or DEFAULT) as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = ROOT
    return cfg


def repo_path(cfg: dict, relative: str) -> str:
    """A path from the config, made absolute against the repo root."""
    return relative if os.path.isabs(relative) else os.path.join(cfg["_root"], relative)
