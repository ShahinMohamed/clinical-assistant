"""Use pinned model files bundled during deployment when available."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def model_location(name, directory):
    bundled = ROOT / "models" / directory
    return str(bundled) if (bundled / "config.json").is_file() else name
