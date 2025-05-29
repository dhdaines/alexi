"""Conversion de PDF en CSV"""

import csv
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, TextIO, Union

import playa
from playa.content import ContentObject, GlyphObject, TextObject
from playa.structure import Element
from playa.pdftypes import Point, Rect
from playa.utils import get_bound_rects

T_obj = Dict[str, Any]
LOGGER = logging.getLogger("convert")
FIELDNAMES = [
    "sequence",
    "segment",
    "text",
    "page",
    "page_width",
    "page_height",
    "fontname",
    "rgb",
    "x0",
    "x1",
    "top",
    "bottom",
    "doctop",
    "mcid",
    "mctag",
    "tagstack",
]


def write_csv(
    doc: Iterable[dict[str, Any]], outfh: TextIO, fieldnames: list[str] = FIELDNAMES
):
    writer = csv.DictWriter(outfh, fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(doc)


def get_rgb(obj: TextObject) -> str:
    """Extraire la couleur d'un objet en chiffres hexadécimaux"""
    ncs = obj.gstate.ncs
    ncolor = obj.gstate.ncolor
    if ncs.ncomponents == 1:
        gray = ncolor.values[0]
        return "#" + "".join(
            ("%x" % int(min(0.999, val) * 16) for val in (gray, gray, gray))
        )
    else:
        return "#" + "".join(
            ("%x" % int(min(0.999, val) * 16) for val in ncolor.values)
        )


def get_tagstack(el: Element) -> str:
    """Extraire la généalogie d'éléments structurels."""
    tags = [el.role]
    parent = el.parent
    while isinstance(parent, Element):
        tags.append(parent.role)
        parent = parent.parent
    return ";".join(reversed(tags))


def word_break(glyph: GlyphObject, origin: Point) -> bool:
    if glyph.text == " ":
        return True
    x, y = glyph.origin
    px, py = origin
    if glyph.font.vertical:
        off = y
        poff = py
    else:
        off = x
        poff = px
    return off - poff > 0.5


def line_break(glyph: GlyphObject, origin: Point) -> bool:
    x, y = glyph.origin
    px, py = origin
    if glyph.font.vertical:
        line_offset = x - px
    else:
        dy = y - py
        if glyph.page.space == "screen":
            line_offset = -dy
        else:
            line_offset = dy
    return line_offset < 0


def make_word(obj: TextObject, text: str, bbox: Rect, pagetop: float) -> T_obj:
    page = obj.page
    x0, y0, x1, y1 = (int(round(x)) for x in bbox)
    word = {
        "text": text,
        "page": page.page_idx,
        "page_width": page.width,
        "page_height": page.height,
        "rgb": get_rgb(obj),
        "x0": x0,
        "x1": x1,
        "top": y0,
        "bottom": y1,
        "doctop": y0 + pagetop,
    }
    if obj.gstate.font is not None:
        word["fontname"] = obj.gstate.font.fontname
    if obj.parent is not None:
        word["tagstack"] = get_tagstack(obj.parent)
    if obj.mcs is not None:
        word["mctag"] = obj.mcs.tag
    if obj.mcid is not None:
        word["mcid"] = obj.mcid
    return word


def iter_words(objs: Iterator[ContentObject], pagetop: float) -> Iterator[T_obj]:
    chars = []
    boxes = []
    next_origin: Union[None, Point] = (0, 0)
    for obj in objs:
        if not isinstance(obj, TextObject):
            continue
        textobj = obj
        for glyph in obj:
            if (
                next_origin is not None
                and chars
                and (word_break(glyph, next_origin) or line_break(glyph, next_origin))
            ):
                yield make_word(
                    textobj, "".join(chars), get_bound_rects(boxes), pagetop
                )
                boxes = []
                chars = []
            if glyph.text != " ":
                chars.append(glyph.text)
                boxes.append(glyph.bbox)
            x, y = glyph.origin
            dx, dy = glyph.displacement
            next_origin = (x + dx, y + dy)
    if chars and textobj:
        yield make_word(textobj, "".join(chars), get_bound_rects(boxes), pagetop)


def extract_words(path: Path) -> Iterator[T_obj]:
    """Extraire mots et traits d'un PDF."""
    with playa.open(path) as pdf:
        # Iterate over text objects grouped by MCID
        pagetop = 0
        for page in pdf.pages:
            yield from iter_words(page, pagetop)
            pagetop += page.height


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()

    write_csv(extract_words(args.pdf), sys.stdout)
