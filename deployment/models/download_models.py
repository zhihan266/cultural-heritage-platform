"""Explicit, resumable download of the original Qwen models. Never run by packaging."""

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=Path(os.getenv("PROJECT_MODELS_DIR", "model_weights")))
    parser.add_argument("--model", choices=["all", "embedding", "reranker"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sources = json.loads(Path(__file__).with_name("model_sources.json").read_text(encoding="utf-8"))
    for name, source in sources.items():
        if args.model not in {"all", name}:
            continue
        destination = args.models_dir.expanduser().resolve() / source["directory"]
        print(json.dumps({"model": source["repo_id"], "revision": source["revision"], "directory": str(destination)}))
        if args.dry_run:
            continue
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=source["repo_id"], revision=source["revision"], local_dir=str(destination))
        (destination / "download-source.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
