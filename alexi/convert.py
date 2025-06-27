"""Conversion de PDF en CSV"""

import csv
import itertools
import logging
from os import cpu_count
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, TextIO, Union

import playa
from playa.content import GlyphObject, TextObject
from playa.pdftypes import Point, Rect
from playa.structure import Element, Tree
from playa.utils import get_bound_rects

T_obj = Dict[str, Any]
LOGGER = logging.getLogger(__name__)


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
    return line_offset < 0 or line_offset > 100  # FIXME: arbitrary!


def make_word(obj: TextObject, text: str, bbox: Rect) -> T_obj:
    page = obj.page
    x0, y0, x1, y1 = (round(x) for x in bbox)
    word = {
        "text": text,
        "page": page.page_idx + 1,
        "page_width": round(page.width),
        "page_height": round(page.height),
        "rgb": get_rgb(obj),
        "x0": x0,
        "x1": x1,
        "top": y0,
        "bottom": y1,
    }
    if obj.gstate.font is not None:
        word["fontname"] = obj.gstate.font.fontname
    try:
        word["tagstack"] = get_tagstack(obj.parent)
    except (TypeError, AttributeError):
        pass
    if obj.mcs is not None:
        word["mctag"] = obj.mcs.tag
    if obj.mcid is not None:
        word["mcid"] = obj.mcid
    return word


def iter_words(objs: playa.Page) -> Iterator[T_obj]:
    chars: List[str] = []
    boxes: List[Rect] = []
    textobj: Union[TextObject, None] = None
    next_origin: Union[None, Point] = (0, 0)
    for obj in objs:
        if not isinstance(obj, TextObject):
            continue
        for glyph in obj:
            if (
                next_origin is not None
                and textobj is not None
                and chars
                and (word_break(glyph, next_origin) or line_break(glyph, next_origin))
            ):
                yield make_word(textobj, "".join(chars), get_bound_rects(boxes))
                boxes = []
                chars = []
                textobj = None
            if glyph.text is not None and glyph.text != " ":
                chars.append(glyph.text)
                boxes.append(glyph.bbox)
                if textobj is None:
                    textobj = obj
            x, y = glyph.origin
            dx, dy = glyph.displacement
            next_origin = (x + dx, y + dy)
    if chars and textobj is not None:
        yield make_word(textobj, "".join(chars), get_bound_rects(boxes))


def extract_page(page: playa.Page) -> List[T_obj]:
    return list(iter_words(page))


class Converteur:
    pdf: playa.Document
    tree: Union[Tree, None]

    def __init__(self, path: Path):
        self.pdf = playa.open(path, max_workers=round(cpu_count() / 2))
        self.tree = self.pdf.structure

    def extract_words(self, pages: Union[List[int], None] = None) -> Iterator[T_obj]:
        """Extraire mots et traits d'un PDF."""
        if pages is None:
            pdfpages = self.pdf.pages
        else:
            pdfpages = self.pdf.pages[[x - 1 for x in pages]]
        return itertools.chain.from_iterable(pdfpages.map(extract_page))
