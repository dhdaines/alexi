"""
Générer des PDF et CSV annotés pour corriger le modèle.
"""

import argparse
import csv
import itertools
import logging
from operator import attrgetter
from pathlib import Path
from typing import Any, Iterable, Iterator, Union

from alexi.analyse import group_iob
from alexi.convert import Converteur, T_obj, write_csv
from alexi.convert_playa import Converteur as ConverteurPlaya
from alexi.label import DEFAULT_MODEL as DEFAULT_LABEL_MODEL
from alexi.label import Identificateur
from alexi.segment import DEFAULT_MODEL as DEFAULT_SEGMENT_MODEL
from alexi.segment import DEFAULT_MODEL_NOSTRUCT, Segmenteur

LOGGER = logging.getLogger(Path(__file__).stem)


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add the arguments to the argparse"""
    parser.add_argument(
        "--segment-model",
        help="Modele CRF",
        type=Path,
    )
    parser.add_argument(
        "--label-model", help="Modele CRF", type=Path, default=DEFAULT_LABEL_MODEL
    )
    parser.add_argument(
        "--pages", help="Liste de numéros de page à extraire, séparés par virgule"
    )
    parser.add_argument(
        "--csv", help="Fichier CSV corrigé pour mettre à jour la visualisation"
    )
    parser.add_argument(
        "--pages-from", help="Fichier CSV donnant les pages a prendre du PDF"
    )
    parser.add_argument(
        "--force", action="store_true", help="Réécrire le fichier CSV même si existant"
    )
    parser.add_argument("--playa", help="Utiliser PLAYA", action="store_true")
    parser.add_argument("--spread", help="Marquer segments sur I", action="store_true")
    parser.add_argument("doc", help="Document en PDF", type=Path)
    parser.add_argument("out", help="Nom de base des fichiers de sortie", type=Path)
    return parser


def annotate_pdf(
    path: Path, pages: Union[list[int], None], iob: list[dict[str, Any]], outpath: Path
) -> None:
    """
    Marquer les blocs de texte extraits par ALEXI dans un PDF.
    """
    import pypdfium2 as pdfium  # type: ignore
    import pypdfium2.raw as pdfium_c  # type: ignore

    pdf = pdfium.PdfDocument(path)
    inpage = 0
    outpage = 0
    if pages:
        for pagenum in pages:
            # Delete up to the current page
            idx = pagenum - 1
            while inpage < idx:
                pdf.del_page(outpage)
                inpage += 1
            # Don't delete the current page :)
            inpage += 1
            outpage += 1
        while len(pdf) > len(pages):
            pdf.del_page(outpage)
    for page, (page_number, group) in zip(
        pdf, itertools.groupby(group_iob(iob), attrgetter("page_number"))
    ):
        page_height = page.get_height()
        LOGGER.info("page %d", page_number)
        for bloc in group:
            x0, top, x1, bottom = bloc.bbox
            width = x1 - x0
            height = bottom - top
            y = page_height - bottom
            LOGGER.info("bloc %s à %d, %d, %d, %d", bloc.type, x0, y, width, height)
            path = pdfium_c.FPDFPageObj_CreateNewRect(
                x0 - 1, y - 1, width + 2, height + 2
            )
            pdfium_c.FPDFPath_SetDrawMode(path, pdfium_c.FPDF_FILLMODE_NONE, True)
            if bloc.type in ("Chapitre", "Annexe"):  # Rouge
                pdfium_c.FPDFPageObj_SetStrokeColor(path, 255, 0, 0, 255)
            elif bloc.type == "Section":  # Rose foncé
                pdfium_c.FPDFPageObj_SetStrokeColor(path, 255, 50, 50, 255)
            elif bloc.type == "SousSection":  # Rose moins foncé
                pdfium_c.FPDFPageObj_SetStrokeColor(path, 255, 150, 150, 255)
            elif bloc.type == "Article":  # Rose clair
                pdfium_c.FPDFPageObj_SetStrokeColor(path, 255, 200, 200, 255)
            elif bloc.type == "Liste":  # Bleu-vert (pas du tout rose)
                pdfium_c.FPDFPageObj_SetStrokeColor(path, 0, 200, 150, 255)
            elif bloc.type in ("Tete", "Pied"):  # Jaunâtre
                pdfium_c.FPDFPageObj_SetStrokeColor(path, 200, 200, 50, 255)
            # Autrement noir
            pdfium_c.FPDFPageObj_SetStrokeWidth(path, 1)
            pdfium_c.FPDFPage_InsertObject(page, path)
        pdfium_c.FPDFPage_GenerateContent(page)
    for page, (page_number, group) in zip(
        pdf, itertools.groupby(group_iob(iob, "sequence"), attrgetter("page_number"))
    ):
        page_height = page.get_height()
        LOGGER.info("page %d", page_number)
        for bloc in group:
            x0, top, x1, bottom = bloc.bbox
            width = x1 - x0
            height = bottom - top
            y = page_height - bottom
            LOGGER.info("element %s à %d, %d, %d, %d", bloc.type, x0, y, width, height)
            path = pdfium_c.FPDFPageObj_CreateNewRect(
                x0 - 1, y - 1, width + 2, height + 2
            )
            pdfium_c.FPDFPath_SetDrawMode(path, pdfium_c.FPDF_FILLMODE_ALTERNATE, False)
            pdfium_c.FPDFPageObj_SetFillColor(path, 50, 200, 50, 50)
            pdfium_c.FPDFPageObj_SetStrokeWidth(path, 1)
            pdfium_c.FPDFPage_InsertObject(page, path)
        pdfium_c.FPDFPage_GenerateContent(page)
    pdf.save(outpath)


def spread_i(iobs: Iterable[T_obj]) -> Iterator[T_obj]:
    """Rétablir les noms des blocs sur les étiquettes I."""
    itor = iter(iobs)
    prev = next(itor)
    yield prev
    for cur in itor:
        bio, sep, name = prev["segment"].partition("-")
        if cur["segment"] == "I":
            cur["segment"] = f"I-{name}"
        yield cur
        prev = cur


def main(args: argparse.Namespace) -> None:
    """Ajouter des anotations à un PDF selon l'extraction ALEXI"""
    if args.pages is not None:
        pages = sorted(int(x.strip()) for x in args.pages.split(","))
    elif args.pages_from is not None:
        pp = set()
        with open(args.pages_from, "r") as infh:
            for row in csv.DictReader(infh):
                pp.add(int(row["page"]))
        pages = sorted(pp)
    else:
        pages = None
    maybe_csv = args.out.with_suffix(".csv")
    if args.csv is None:
        if maybe_csv.exists() and not args.force:
            LOGGER.warning(
                "Utilisation du fichier CSV déjà existant: %s "
                "(pour réecrire ajouter --force)",
                maybe_csv,
            )
            args.csv = maybe_csv
    if args.csv is not None:
        with open(args.csv, "rt", encoding="utf-8-sig") as infh:
            iob = list(csv.DictReader(infh))
    else:
        args.csv = maybe_csv
        if args.segment_model is not None:
            crf = Segmenteur(args.segment_model)
            crf_n = crf
        else:
            crf = Segmenteur(DEFAULT_SEGMENT_MODEL)
            crf_n = Segmenteur(DEFAULT_MODEL_NOSTRUCT)
        crf_s = Identificateur(args.label_model)
        if args.playa:
            conv: Union[Converteur, ConverteurPlaya] = ConverteurPlaya(args.doc)
        else:
            conv = Converteur(args.doc)
        feats = conv.extract_words(pages)
        if conv.tree is None:
            LOGGER.warning("Structure logique absente: %s", args.doc)
            segs = crf_n(feats)
        else:
            segs = crf(feats)
        iob = crf_s(segs)
        if args.spread:
            iob = spread_i(iob)
        iob = list(iob)
        with open(args.csv, "wt") as outfh:
            write_csv(iob, outfh)
    annotate_pdf(args.doc, pages, iob, args.out.with_suffix(".pdf"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    # Done by top-level alexi if not running this as script
    parser.add_argument(
        "-v", "--verbose", help="Notification plus verbose", action="store_true"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    main(args)
