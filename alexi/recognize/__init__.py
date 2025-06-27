"""Reconnaissance d'objets textuels avec modèles de vision.

Ce repertoire regroupe quelques détecteurs de mise en page pour faire
la pré-segmentation des documents.  Cet étape est utilisée pour
séparer les images et tableaux du texte pour un traitement séparé
(pour le moment ceci consiste à les convertir en images, mais les
tableaux seront pris en charge autrement à l'avenir).

Cette étape est facultative, et vous pouvez toujours utiliser la
détection par défaut qui utilise la structure explicit du PDF (mais
celle-ci n'est pas toujours présente ni correcte).
"""

import logging
from os import PathLike
from pathlib import Path
from typing import Iterable, Iterator, Type, Union

from alexi.analyse import Bloc

LOGGER = logging.getLogger(Path(__file__).stem)


class Objets:
    """Classe de base pour les détecteurs d'objects."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __call__(
        self,
        pdf_path: Union[str, PathLike],
        pages: Union[None, Iterable[int]] = None,
        labelmap: Union[None, dict] = None,
    ) -> Iterator[Bloc]:
        yield from ()

    @classmethod
    def byname(cls, name: str) -> Type["Objets"]:
        # We do not want to use a dictionary here because these should
        # be imported lazily (so as to avoid dependencies)
        if name == "docling":
            from alexi.recognize.docling import ObjetsDocling

            return ObjetsDocling
        else:
            from alexi.recognize.playa import ObjetsPlaya

            return ObjetsPlaya
