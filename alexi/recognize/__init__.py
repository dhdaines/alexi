"""Reconnaissance d'objets textuels avec modèles de vision.

Ce repertoire regroupe quelques détecteurs de mise en page pour faire
la pré-segmentation des documents.  Cet étape est utilisée pour
séparer les images et tableaux du texte pour un traitement séparé
(pour le moment ceci consiste à les convertir en images, mais les
tableaux seront pris en charge autrement à l'avenir).

Puisque les conditions d'utilisation sont plus restrictives pour
certains modèles dont YOLOv8, cette étape est facultative, et vous
pouvez toujours utiliser la détection par défaut qui utilise la
structure explicit du PDF (mais celle-ci n'est pas toujours présente
ni correcte)."""

import logging
from pathlib import Path
from typing import Type

LOGGER = logging.getLogger(Path(__file__).stem)


class Objets:
    """Classe de base pour les détecteurs d'objects."""

    @classmethod
    def byname(cls, name: str) -> Type["Objets"]:
        # We do not want to use a dictionary here because these should
        # be imported lazily (so as to avoid dependencies)
        if name == "yolo":
            from alexi.recognize.yolo import ObjetsYOLO

            return ObjetsYOLO
        elif name == "docling":
            from alexi.recognize.docling import ObjetsDocling

            return ObjetsDocling
        else:
            from alexi.recognize.playa import ObjetsPlaya

            return ObjetsPlaya
