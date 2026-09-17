"""Download pretrained GloVe (Wikipedia + Gigaword, 300d) and cache the 100k most frequent words.
Production swaps to BAAI/bge-base-en-v1.5 (EMBEDDING_PROVIDER=bge, EMBEDDING_DIM=768) and re-indexes."""
import gzip
import json
import urllib.request
from pathlib import Path

import numpy as np

URL = "https://github.com/RaRe-Technologies/gensim-data/releases/download/glove-wiki-gigaword-300/glove-wiki-gigaword-300.gz"
OUT = Path(__file__).resolve().parents[1] / "models" / "embeddings"


def main(top_n: int = 100_000) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    gz = OUT / "glove300.gz"
    if not gz.exists():
        print("downloading", URL)
        urllib.request.urlretrieve(URL, gz)  # noqa: S310  # nosec B310 - fixed https URL
    vocab, vecs = [], []
    with gzip.open(gz, "rt", encoding="utf8") as f:
        next(f)
        for i, line in enumerate(f):
            if i >= top_n:
                break
            parts = line.rstrip().split(" ")
            vocab.append(parts[0])
            vecs.append(np.asarray(parts[1:], dtype=np.float32))
    np.save(OUT / "glove300_100k.npy", np.vstack(vecs))
    (OUT / "glove300_100k_vocab.json").write_text(json.dumps(vocab))
    gz.unlink()
    print("cached", len(vocab), "vectors in", OUT)


if __name__ == "__main__":
    main()
