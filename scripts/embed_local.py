#!/usr/bin/env python3
"""Optional local-only embedding command; stdin texts -> stdout vectors.

Install sentence-transformers in a dedicated environment and supply an already
downloaded model directory. This script neither needs nor uses an LLM API key.
"""
import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Existing local model directory")
    args = parser.parse_args()
    if not os.path.isdir(args.model):
        parser.error("--model must name an existing local directory; no implicit downloads")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer

    request = json.load(sys.stdin)
    texts = request["texts"]
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise ValueError("texts must be an array of strings")
    model = SentenceTransformer(args.model, device="cpu", local_files_only=True)
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    json.dump({"vectors": vectors.tolist()}, sys.stdout, allow_nan=False)


if __name__ == "__main__":
    main()
