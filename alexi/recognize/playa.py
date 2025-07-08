import itertools
import logging
import operator
import re
from os import PathLike, cpu_count
from pathlib import Path
from typing import Dict, Iterable, Iterator, Union, cast

import playa
from playa import Document, Page, PageList
from playa.pdftypes import Rect
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


def make_blocs(el: Element, pages: Dict[Page, Dict[int, Rect]]) -> Iterator[Bloc]:
    """Générer les blocs correspondant à un élément s'ils se trouvent
    dans l'ensemble de pages recherchées"""
    if el.page is not None and el.page not in pages:
        return
    role = el.role
    LOGGER.info("%s on pages:", role)
    for page, items in itertools.groupby(
        content_items(el), operator.attrgetter("page")
    ):
        if page not in pages:
            continue
        # FIXME: Possible Form XObjects (in the hell)
        mcids = set(item.mcid for item in items)
        LOGGER.info("    page %d mcids %r", page.page_idx + 1, mcids)
        boxes = pages[page]
        bbox = cast(
            Rect,
            tuple(
                int(round(x))
                for x in get_bound_rects(boxes[mcid] for mcid in mcids if mcid in boxes)
            ),
        )
        LOGGER.info("Got BBox on page %d from contents: %r", page.page_idx + 1, bbox)
        yield Bloc(
            type="Tableau" if role == "Table" else role,
            contenu=[],
            _page_number=page.page_idx + 1,
            _bbox=bbox,
        )


def get_mcid_boxes(page: Page) -> Dict[int, Rect]:
    boxes = {}
    for mcid, objs in itertools.groupby(page, operator.attrgetter("mcid")):
        boxes[mcid] = get_bound_rects(obj.bbox for obj in objs)
    return boxes


def pagelist_boxes(pdf: Document, pagelist: Iterable[Page]) -> Iterator[Bloc]:
    if pdf.structure is None:
        return
    LOGGER.info("Calcul des rectangles de contenu sur pages")
    if isinstance(pagelist, PageList):
        mcid_boxes = pagelist.map(get_mcid_boxes)
    else:
        mcid_boxes = map(get_mcid_boxes, pagelist)
    page_boxes = {page: boxes for page, boxes in zip(pagelist, mcid_boxes)}
    LOGGER.info("Extraction des éléments visuels:")
    for el in pdf.structure.find_all(re.compile("^(?:Table|Figure)$")):
        yield from make_blocs(el, page_boxes)


class ObjetsPlaya(Objets):
    def __call__(
        self,
        pdf_or_path: Union[str, Page, PageList, PathLike],
        pages: Union[None, Iterable[int]] = None,
        labelmap: Union[None, dict] = None,
    ) -> Iterator[Bloc]:
        """Extraire les rectangles correspondant aux objets qui seront
        représentés par des images."""
        ncpu = cpu_count()
        ncpu = 1 if ncpu is None else round(ncpu / 2)
        if isinstance(pdf_or_path, Page):
            return pagelist_boxes(pdf_or_path.doc, [pdf_or_path])
        elif isinstance(pdf_or_path, PageList):
            return pagelist_boxes(pdf_or_path.doc, pdf_or_path)
        with playa.open(pdf_or_path, max_workers=ncpu) as pdf:
            pagelist = pdf.pages if pages is None else pdf.pages[(x - 1 for x in pages)]
            return pagelist_boxes(pdf, pagelist)
