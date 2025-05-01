import logging
from os import PathLike
from pathlib import Path
import re
from typing import Iterable, Iterator, Set, Union
import playa
from playa import Page
from playa.structure import Element

from alexi.analyse import Bloc
from alexi.recognize import Objets

LOGGER = logging.getLogger(Path(__file__).stem)


def make_blocs(el: Element, pages: Set[Page]) -> Iterator[Bloc]:
    """Générer les blocs correspondant à un élément s'ils se trouvent
    dans l'ensemble de pages recherchées"""
    


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
            pageset = set(pdf.pages) if pages is None else set(pdf.pages[x-1] for x in pages)
            for el in pdf.structure.find_all(re.compile(r"Table|Figure")):
                yield from make_blocs(el, pageset)