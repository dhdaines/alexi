"""
Analyser un document étiquetté pour en extraire la structure.
"""

import itertools
import logging
import operator
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple, Optional, Union

from playa import Rect

from alexi.convert import T_obj

LOGGER = logging.getLogger("analyse")

EXTENSION = "jpg"  # Ou png, si désiré (FIXME: webm...)


class Hyperlien(NamedTuple):
    """Hyperlien dans un bloc de texte."""

    start: int
    end: int
    alt: Optional[str]
    href: Optional[str]


@dataclass
class Bloc:
    """Élément de présentation (bloc de texte ou image)"""

    type: str
    contenu: list[T_obj]
    liens: Optional[list[Hyperlien]] = None
    _bbox: Optional[Rect] = None
    _page_number: Optional[int] = None

    def __hash__(self) -> int:
        if self._bbox:
            return hash((self.type, self._bbox, self._page_number))
        else:
            return hash((self.type, self.texte))

    @property
    def texte(self) -> str:
        """Représentation textuel du bloc."""
        return " ".join(x["text"] for x in self.contenu)

    @property
    def page_number(self) -> int:
        """Numéro de page de ce bloc."""
        if self._page_number is not None:
            return self._page_number
        return int(self.contenu[0]["page"])

    @property
    def bbox(self) -> Rect:
        if self._bbox is not None:
            return self._bbox
        return merge_bboxes(
            (int(word["x0"]), int(word["top"]), int(word["x1"]), int(word["bottom"]))
            for word in self.contenu
        )

    @property
    def img(self) -> str:
        bbox = ",".join(str(round(x)) for x in self.bbox)
        return f"page{self.page_number}-{bbox}.{EXTENSION}"


# For the moment we will simply use regular expressions but this
# should be done with the sequence CRF
SECTION = r"\b(?:article|chapitre|section|sous-section|annexe)s?"
NUMERO = r"[\d\.XIV]+"
NUMEROS = rf"{NUMERO}(?P<numeros>(?:,|\s+(?:et|ou))\s+{NUMERO})*"
MILIEU = r"\btypes?\s+des?\s+milieux?"
MTYPE = r"[\dA-Z]+\.\d"
MTYPES = rf"{MTYPE}(?P<mtypes>(?:,|\s+(?:et|ou))\s+{MTYPE})*"
RLRQ = r"(?:c\.|(?:R\.?\s*)?[LR]\.?\s*R\.?\s*Q\.?)\s*,?[^\)]+"
REGNUM = rf"(?:(?:SQ-)?\d[\d\.A-Z-]+|\({RLRQ}\))"
REGLEMENT = rf"""
règlement\s+
(?:
   {REGNUM}
  |de\s+zonage
  |de\s+lotissement
  |de\s+construction
  |(?:sur\s+les|relatif\s+aux)\s+(?:PIIA|plans\s+d['’]implantation\s+et\s+d['’]intégration\s+architecturale)
  |(?:sur\s+les|relatif\s+aux)\s+permis\s+et\s+(?:les\s+)?certificats
  |(?:de|sur|concernant).*?{REGNUM}
)"""
LOI = rf"""
(?:code\s+civil
  |(?:loi|code)\s+.*?\({RLRQ}\)
  |loi\s+sur\s+l['’]aménagement\s+et\s+l['’]urbanisme
  |loi\s+sur\s+la\s+qualité\s+de\s+l['’]environnement
  |loi\s+sur\s+les\s+cités\s+et\s+villes
)"""
DU = r"(?:du|de\s+l['’]|de\s+la)"
MATCHER = re.compile(
    rf"""
(?:
   (?:{SECTION}\s+(?P<numero>{NUMEROS})
      (?:\s+{DU}\s+{SECTION}\s+{NUMERO})*
     |{MILIEU}\s+(?P<mtype>{MTYPES}))
   (?:\s+{DU}\s+(?:{REGLEMENT}|{LOI}))?
  |{REGLEMENT}|{LOI})
    """,
    re.IGNORECASE | re.VERBOSE,
)
NUMMATCH = re.compile(NUMERO, re.IGNORECASE | re.VERBOSE)
MTMATCH = re.compile(MTYPE, re.IGNORECASE | re.VERBOSE)


def match_links(text: str):
    """
    Identifier des hyperliens potentiels dans un texte.
    """
    for m in MATCHER.finditer(text):
        if m.group("numeros") is not None:
            before = re.sub(r"s$", "", text[m.start() : m.start("numero")].strip())
            after = text[m.end("numero") : m.end()]
            for num in NUMMATCH.finditer(m.group("numero")):
                yield Hyperlien(
                    m.start("numero") + num.start(),
                    m.start("numero") + num.end(),
                    f"{before} {num.group()}{after}",
                    None,
                )
        elif m.group("mtypes") is not None:
            before = text[m.start() : m.start("mtype")]
            after = text[m.end("mtype") : m.end()]
            for mt in MTMATCH.finditer(m.group("mtype")):
                yield Hyperlien(
                    m.start("mtype") + mt.start(),
                    m.start("mtype") + mt.end(),
                    f"{before}{mt.group()}{after}",
                    None,
                )
        else:
            yield Hyperlien(m.start(), m.end(), None, None)


def group_iob(words: Iterable[T_obj], key: str = "segment") -> Iterator[Bloc]:
    """Regrouper mots en blocs de texte selon leurs étiquettes IOB."""
    bloc = Bloc(type="", contenu=[])
    for word in words:
        bio, sep, tag = word[key].partition("-")
        if bio in ("B", "O"):
            if bloc.type != "":
                yield bloc
            # Could create an empty tag if this is O
            bloc = Bloc(type=tag, contenu=[])
        elif bio == "I":
            # Sometimes we are missing an initial B
            if bloc.type == "":
                bloc.type = tag
        else:
            raise ValueError("Tag %s n'est pas I, O ou B: %s" % (word[key], word))
        if bio != "O":
            bloc.contenu.append(word)
    if bloc.type != "":
        yield bloc


PALIERS = [
    "Document",
    "Annexe",
    "TitreS",
    "Chapitre",
    "Section",
    "SousSection",
    "Article",
]


@dataclass
class Element:
    type: str
    titre: str = ""
    numero: str = ""
    debut: int = 0
    fin: int = -1
    sub: list["Element"] = field(default_factory=list)
    page: int = 1

    # NOTE: pydantic would do this automatically, seems dataclasses don't
    @classmethod
    def fromdict(self, **kwargs) -> "Element":
        kwargs["sub"] = [Element.fromdict(**subel) for subel in kwargs.get("sub", [])]
        el = Element(**kwargs)
        return el

    def traverse(self) -> Iterator[tuple[list[str], "Element"]]:
        """Pre-order traversal of the subtree."""
        d: deque[Element | None] = deque(self.sub)
        path: list[str] = []
        while d:
            el = d.popleft()
            if el is None:
                path.pop()
                path.pop()
                continue
            yield path, el
            if el.sub:
                path.append(el.type)
                path.append(el.numero)
                d.appendleft(None)
                d.extendleft(reversed(el.sub))


ELTYPE = r"(?i:article|chapitre|section|sous-section|titre|annexe)"
DOTSPACEDASH = r"(?:\.|\s*[:—–-]| )"
NUM = r"(\d+)"
NUMDOT = r"((?:\d+\.)+\d+)"
ALPHA = r"[A-Z]"
ROMAN = r"[XIV]+"
NUMRE = re.compile(
    rf"{ELTYPE}?\s*"
    r"(?:"
    rf"{NUMDOT}{DOTSPACEDASH}?"
    r"|"
    rf"{NUM}{DOTSPACEDASH}?"
    r"|"
    rf"({ALPHA}|{ROMAN}){DOTSPACEDASH}"
    r")"
    r"\s*"
)
NUMENDRE = re.compile(rf".*\b{NUM}{DOTSPACEDASH}\s*$")


@dataclass(init=False)
class Document:
    """Document avec blocs de texte et structure."""

    fileid: str
    meta: dict[str, list[str]]
    paliers: dict[str, list[Element]]
    contenu: list[Bloc]
    unknown_id: int = 0
    pdfurl: Optional[str] = None

    def __init__(self, fileid: str, numero: str = "", titre: str = "Document") -> None:
        self.fileid = fileid
        self.paliers = {}
        self.meta = {}
        self.contenu = []
        doc = Element(type="Document", titre=titre, numero=numero)
        self.paliers.setdefault("Document", []).append(doc)

    @classmethod
    def fromdict(self, data: dict) -> "Document":
        doc = Document(data["fileid"])
        doc.paliers = {
            key: [Element.fromdict(**el) for el in value]
            for key, value in data["paliers"].items()
        }
        doc.meta = data["meta"]
        doc.contenu = [Bloc(**value) for value in data["contenu"]]
        return doc

    def extract_numero(self, titre: str) -> tuple[str, str]:
        """Extraire le numero d'un article/chapitre/section/annexe, si possible."""
        # FIXME: UNIT TEST THIS!!!
        if m := NUMRE.match(titre):
            if m.group(1):  # sous section (x.y.z)
                numero = m.group(1)
            elif m.group(2):  # article (x)
                numero = m.group(2)
            elif m.group(3):  # annexe (A), chapitre (III)
                numero = m.group(3)
            else:
                numero = "_%d" % self.unknown_id
                self.unknown_id += 1
            titre = titre[m.end(0) :]
        elif m := NUMENDRE.match(titre):
            numero = m.group(1)
            titre = titre[: m.start(1)]
        else:
            numero = "_%d" % self.unknown_id
            self.unknown_id += 1
        return numero, titre

    def add_bloc(self, bloc: Bloc):
        """Ajouter un bloc de texte."""
        if bloc.type in PALIERS:
            numero, titre = self.extract_numero(bloc.texte)
            element = Element(
                type=bloc.type,
                titre=titre,
                numero=numero,
                debut=len(self.contenu),
                fin=-1,
                sub=[],
                page=int(bloc.page_number),
            )
            self.add_element(element)
        else:
            bloc.liens = list(match_links(bloc.texte))
            if bloc.liens:
                LOGGER.info(
                    "Liens potentiels trouvés: %s",
                    ",".join(bloc.texte[li.start : li.end] for li in bloc.liens),
                )
            self.contenu.append(bloc)

    def add_element(self, element: Element):
        """Ajouter un élément au palier approprié."""
        # Fermer l'élément précédent du paliers actuel et inférieurs
        pidx = PALIERS.index(element.type)
        for palier in PALIERS[pidx:]:
            if palier in self.paliers and self.paliers[palier]:
                previous = self.paliers[palier][-1]
                if previous.fin == -1:
                    previous.fin = element.debut
        # Ajouter l'élément au palier actuel
        self.paliers.setdefault(element.type, []).append(element)
        # Ajouter à un élément supérieur s'il existe est s'il est ouvert
        if pidx == 0:
            return
        for palier in PALIERS[pidx - 1 :: -1]:
            if palier in self.paliers and self.paliers[palier]:
                previous = self.paliers[palier][-1]
                if previous.fin == -1:
                    previous.sub.append(element)
                    break

    @property
    def structure(self) -> Element:
        """Racine de l'arborescence structurel du document."""
        return self.paliers["Document"][0]

    @property
    def titre(self) -> str:
        """Titre du document."""
        return self.structure.titre

    @property
    def numero(self) -> str:
        """Numero du document."""
        return self.structure.numero


def bbox_overlaps(obox: Rect, bbox: Rect) -> bool:
    """Déterminer si deux BBox ont une intersection."""
    ox0, otop, ox1, obottom = obox
    x0, top, x1, bottom = bbox
    return ox0 < x1 and ox1 > x0 and otop < bottom and obottom > top


def calculate_area(box: Rect) -> float:
    x0, y0, x1, y1 = box
    return abs((x1 - x0) * (y1 - y0))


def merge_bboxes(boxes: Iterable[Rect]) -> Rect:
    x0, y0, x1, y1 = zip(*boxes)
    return (
        min(min(x0), min(x1)),
        min(min(y0), min(y1)),
        max(max(x0), max(x1)),
        max(max(y0), max(y1)),
    )


def merge_overlaps(images: Iterable[Bloc]) -> list[Bloc]:
    """Fusionner des blocs qui se touchent en préservant l'ordre"""
    # FIXME: preserving order maybe not necessary :)
    ordered_images = list(enumerate(images))
    ordered_images.sort(key=lambda x: -calculate_area(x[1].bbox))
    while True:
        nimg = len(ordered_images)
        new_ordered_images = []
        overlapping = {}
        for idx, image in ordered_images:
            # Must always merge with an image bloc (FIXME: configurable?)
            if image.type not in ("Tableau", "Figure"):
                continue
            for ydx, other in ordered_images:
                if other is image:
                    continue
                # Do not merge non-displaying blocs (FIXME: configurable?)
                if other.type in ("Tete", "Pied", "TOC"):
                    continue
                if bbox_overlaps(image.bbox, other.bbox):
                    overlapping[ydx] = other
            if overlapping:
                big_box = merge_bboxes(
                    (image.bbox, *(other.bbox for other in overlapping.values()))
                )
                LOGGER.info(
                    "%s @ %r overlaps %s merged to %s"
                    % (
                        image.type,
                        image.bbox,
                        ", ".join(
                            ("%s @ %r" % (other.type, other.bbox))
                            for other in overlapping.values()
                        ),
                        big_box,
                    )
                )
                bloc_types = set(
                    bloc.type
                    for bloc in itertools.chain((image,), overlapping.values())
                )
                image_type = "Tableau" if "Tableau" in bloc_types else "Figure"
                new_image = Bloc(
                    type=image_type,
                    contenu=list(
                        itertools.chain(
                            image.contenu,
                            *(other.contenu for other in overlapping.values()),
                        )
                    ),
                    _bbox=big_box,
                    _page_number=image._page_number,
                )
                for oidx, image in ordered_images:
                    if oidx == idx:
                        new_ordered_images.append((idx, new_image))
                    elif oidx in overlapping:
                        pass
                    else:
                        new_ordered_images.append((oidx, image))
                break
        if overlapping:
            ordered_images = new_ordered_images
        if len(ordered_images) == nimg:
            break
    ordered_images.sort()
    return [img for _, img in ordered_images]


class Analyseur:
    """Analyse d'un document étiqueté en IOB."""

    def __init__(self, fileid: str, words: Iterable[T_obj]):
        self.fileid = fileid
        self.words: list[T_obj] = list(words)
        self.blocs: list[Bloc] = list(group_iob(self.words, "segment"))
        self.metadata: dict[str, list[str]] = {}
        for bloc in group_iob(self.words, "sequence"):
            LOGGER.info(f"sequence {bloc.type}: {bloc.texte}")
            self.metadata.setdefault(bloc.type, []).append(bloc.texte)

    def add_images(self, images: Iterable[Bloc], merge: bool = True):
        """Insérer les images en les fusionnant avec le texte (et entre elles)
        si demandé."""
        images_bypage: dict[int, list[Bloc]] = {
            page_number: list(group)
            for page_number, group in itertools.groupby(
                images, operator.attrgetter("page_number")
            )
        }

        # FIXME: assume that we can order things this way!
        def bbox_order(bloc):
            x0, top, x1, bottom = bloc.bbox
            return (top, x0, bottom, x1)

        new_blocs: list[Bloc] = []
        for page_number, group in itertools.groupby(
            self.blocs, operator.attrgetter("page_number")
        ):
            if page_number in images_bypage:
                page_blocs = list(group)
                page_blocs.extend(images_bypage[page_number])
                page_blocs.sort(key=bbox_order)
                if merge:
                    LOGGER.info("Merging blocs on page %d:", page_number)
                    for bloc in page_blocs:
                        LOGGER.info("\t%s @ %r", bloc.type, bloc.bbox)
                    new_blocs.extend(merge_overlaps(page_blocs))
                else:
                    new_blocs.extend(page_blocs)
            else:
                new_blocs.extend(group)  # noqa: B031
        self.blocs = new_blocs

    def __call__(
        self,
        blocs: Optional[Iterable[Bloc]] = None,
    ) -> Document:
        """Analyse du structure d'un document."""
        titre = self.metadata.get("Titre", ["Document"])[0]
        numero = self.metadata.get("Numero", [""])[0]
        if m := re.search(r"(?i:num[ée]ro)\s+([0-9][A-Z0-9-]+)", titre):
            LOGGER.info("Numéro extrait du titre: %s", m.group(1))
            numero = m.group(1)
            titre = titre[: m.start(0)] + titre[m.end(0) :]
        elif m := re.search(r"(?i:r[èe]glement)\s+([0-9][A-Z0-9-]+)", titre):
            LOGGER.info("Numéro extrait du titre: %s", m.group(1))
            numero = m.group(1)
            titre = titre[: m.start(1)] + titre[m.end(1) :]
        elif m := re.match(r".*(\b\d+-\d+-[A-Z]+$)", titre):
            LOGGER.info("Numéro extrait du titre: %s", m.group(1))
            numero = m.group(1)
            titre = titre[: m.start(1)]
        doc = Document(self.fileid, numero, titre)
        doc.meta = self.metadata
        if blocs is None:
            blocs = self.blocs
        for bloc in blocs:
            doc.add_bloc(bloc)
        return doc


def extract_zonage(doc: Document) -> Union[dict[str, dict[str, dict[str, str]]], None]:
    """
    Extraire les éléments du zonage d'un règlement et générer des
    metadonnées pour l'identification des hyperliens et la
    présentation dans ZONALDA.
    """
    mz: Optional[Element] = None
    if "Chapitre" not in doc.paliers:
        LOGGER.warning("Aucun chapitre présent dans %s", doc.fileid)
        return None
    for c in doc.paliers["Chapitre"]:
        if "milieux et zones" in c.titre.lower():
            LOGGER.info("Extraction de milieux et zones")
            mz = c
            break
    if mz is None:
        LOGGER.info("Chapitre milieux et zones non trouvé")
        return None
    top = Path(doc.fileid) / "Chapitre" / mz.numero
    metadata: dict[str, dict[str, dict[str, str]]] = {
        "categorie_milieu": {},
        "milieu": {},
    }
    for sec in mz.sub:
        if "dispositions" in sec.titre.lower():
            continue
        secdir = top / sec.type / sec.numero
        if m := re.match(r"\s*(\S+)\s*[-–—]\s*(.*)", sec.titre):
            metadata["categorie_milieu"][m.group(1)] = {
                "titre": m.group(2),
                "url": str(secdir),
            }
        for subsec in sec.sub:
            subsecdir = secdir / subsec.type / subsec.numero
            if m := re.match(r"\s*(\S+)[-–—\s]+(.*)", subsec.titre):
                metadata["milieu"][m.group(1)] = {
                    "titre": m.group(2),
                    "url": str(subsecdir),
                }
    if len(metadata["categorie_milieu"]) == 0 and len(metadata["milieu"]) == 0:
        return None
    return metadata
