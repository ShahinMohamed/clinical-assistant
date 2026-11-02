"""Build the explicit public asset directory and private, pinned CPU models."""

import hashlib
import json
import os
import shutil
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
ASSETS = ("index.html", "styles.css", "app.js", "favicon.svg")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    PUBLIC.mkdir(exist_ok=True)
    for path in PUBLIC.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for name in ASSETS:
        shutil.copy2(ROOT / "web" / "static" / name, PUBLIC / name)
    # .env, Python code, model files and datasets never enter the public folder.
    # Standard-library build: dependency installation for Functions is separate.
    for model in json.loads((ROOT / "docs" / "model-bundles.json").read_text()):
        for item in model["files"]:
            target = ROOT / "models" / model["directory"] / item["path"]
            if target.is_file() and digest(target) == item["sha256"]:
                continue
            if os.getenv("BUILD_MODELS_OFFLINE") == "1":
                raise RuntimeError("A verified model file is missing from the local build.")
            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://huggingface.co/{model['repository']}/resolve/{model['revision']}/{item['path']}"
            temporary = target.with_suffix(target.suffix + ".download")
            with urlopen(url, timeout=120) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            if digest(temporary) != item["sha256"]:
                temporary.unlink()
                raise RuntimeError("Downloaded model checksum differs from the pinned bundle.")
            temporary.replace(target)
    print("Public assets and private model bundles prepared.")


if __name__ == "__main__":
    main()
