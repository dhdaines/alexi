import itertools
import logging
import operator
import re
from os import PathLike
from pathlib import Path
from typing import Iterable, Iterator, Set, Union

import playa
from playa import Page
from playa.structure import ContentItem, Element
from playa.utils import get_bound_rects

from alexi.analyse import Bloc
from alexi.recognize import Objets

LOGGER = logging.getLogger(Path(__file__).stem)


def content_items(el: Element) -> Iterator[ContentItem]:
    """Rassembler les contenus d'un élément,"""
    for kid in el:
        if isinstance(kid, Element):
            yield from content_items(kid)
        elif isinstance(kid, ContentItem):
            yield kid


def make_blocs(el: Element, pages: Set[Page]) -> Iterator[Bloc]:
    """Générer les blocs correspondant à un élément s'ils se trouvent
    dans l'ensemble de pages recherchées"""
    if el.page is not None and el.page not in pages:
        return
    if el.page is not None and "BBox" in el.props:
        yield Bloc(
            type="Tableau" if el.type == "Table" else el.type,
            contenu=[],
            _page_number=el.page.page_idx + 1,
            _bbox=el.bbox,
        )
    else:
        # FIXME: Potentially inefficient since we need to iterate over
        # the page for every element, *but* it is considerably more
        # complex to iterate over pages as we have to construct mappings
        for page, items in itertools.groupby(
            content_items(el), operator.attrgetter("page")
        ):
            if page not in pages:
                continue
            yield Bloc(
                type="Tableau" if el.type == "Table" else el.type,
                contenu=[],
                _page_number=page.page_idx + 1,
                _bbox=get_bound_rects(item.bbox for item in items),
            )


class ObjetsPlaya(Objets):
    def __call__(
        self,
        pdf_path: Union[str, PathLike],
        pages: Union[None, Iterable[int]] = None,
        labelmap: Union[None, dict] = None,
    ) -> Iterator[Bloc]:
        """Extraire les rectangles correspondant aux objets qui seront
        représentés par des images."""
        pdf_path = Path(pdf_path)
        with playa.open(pdf_path) as pdf:
            if pdf.structure is None:
                return
            pageset = (
                set(pdf.pages)
                if pages is None
                else set(pdf.pages[x - 1] for x in pages)
            )
            for el in pdf.structure.find_all(re.compile(r"^(?:Table|Figure)$")):
                yield from make_blocs(el, pageset)
