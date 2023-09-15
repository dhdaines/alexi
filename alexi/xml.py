"""
Convertir structure d'un document en XML.
"""

from typing import Any, Iterable, Optional

import lxml.etree as ET

from .label import line_breaks


def iob2xml(labeled: Iterable[dict[str, Any]]) -> Any:  # lxml type checking is not good
    root = ET.Element("ALEXI")
    tree = ET.ElementTree(root)
    element: Optional[Any] = None
    words: list[dict[str, Any]] = []
    for word in labeled:
        bio, sep, tag = word["tag"].partition("-")
        if bio == "B" or (element is None and tag != ""):
            if element is not None:
                if words:
                    element.text = "\n".join(
                        " ".join(w["text"] for w in line) for line in line_breaks(words)
                    )
                root.append(element)
                element = None
            words = []
            element = ET.Element(tag)
        if bio != "O":
            words.append(word)
    return tree