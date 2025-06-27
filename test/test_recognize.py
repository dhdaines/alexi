from pathlib import Path

import pytest
from playa import Rect

from alexi.convert import Converteur, T_obj
from alexi.extract import LABELMAP
from alexi.recognize.playa import ObjetsPlaya

try:
    from alexi.recognize.docling import ObjetsDocling
except ImportError:
    ObjetsDocling = None

DATADIR = Path(__file__).parent / "data"


def obj_to_bbox(obj: T_obj) -> Rect:
    return (obj["x0"], obj["top"], obj["x1"], obj["bottom"])


def bbox_contains(bbox: Rect, ibox: Rect) -> bool:
    """Déterminer si une BBox est contenu entièrement par une autre."""
    x0, top, x1, bottom = bbox
    ix0, itop, ix1, ibottom = ibox
    return ix0 >= x0 and ix1 <= x1 and itop >= top and ibottom <= bottom


def test_extract_tables_and_figures_playa() -> None:
    conv = Converteur(DATADIR / "pdf_figures.pdf")
    obj = ObjetsPlaya()
    words = list(conv.extract_words())
    images = list(obj(DATADIR / "pdf_figures.pdf", labelmap=LABELMAP))
    assert len(images) == 2
    table = next(img for img in images if img.type == "Tableau")
    figure = next(img for img in images if img.type == "Figure")
    for w in words:
        if bbox_contains(table.bbox, obj_to_bbox(w)):
            assert "Table" in w["tagstack"]
        if bbox_contains(figure.bbox, obj_to_bbox(w)):
            assert "Figure" in w["tagstack"]


@pytest.mark.skipif(ObjetsDocling is None, reason="Docling has flown the coop")
def test_extract_tables_and_figures_docling() -> None:
    conv = Converteur(DATADIR / "pdf_figures.pdf")
    obj = ObjetsDocling()
    words = list(conv.extract_words())
    images = list(obj(DATADIR / "pdf_figures.pdf", labelmap=LABELMAP))
    table = next(img for img in images if img.type == "Tableau")
    figure = next(img for img in images if img.type == "Figure")
    for w in words:
        if bbox_contains(table.bbox, obj_to_bbox(w)):
            assert "Table" in w["tagstack"]
        if bbox_contains(figure.bbox, obj_to_bbox(w)):
            assert "Figure" in w["tagstack"]
