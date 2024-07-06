"""
Lancer des recherches dans l'index de données.
"""

import json
from pathlib import Path
from typing import List

from lunr.index import Index  # type: ignore
from lunr.languages import get_nltk_builder  # type: ignore

from alexi.index import unifold

# This is just here to register the necessary pipeline functions
get_nltk_builder(["fr"])


def search(indexdir: Path, terms: List[str], nresults: int) -> None:
    with open(indexdir / "index.json", "rt", encoding="utf-8") as infh:
        index = Index.load(json.load(infh))
    index.pipeline.add(unifold)
    results = index.search(" ".join(terms))
    for idx, r in enumerate(results):
        if idx == nresults:
            break
        print(r)
