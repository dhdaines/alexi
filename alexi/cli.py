"""ALexi, EXtracteur d'Information

Ce module est le point d'entrée principale pour l'outil de ligne de
commande ALEXI.
"""

import argparse
import csv
import dataclasses
import json
import logging
import sys
from pathlib import Path
from typing import Union

LOGGER = logging.getLogger("alexi")


def convert_main(args: argparse.Namespace):
    """Convertir les PDF en CSV"""
    from .convert import Converteur, write_csv
    from .convert_playa import Converteur as ConverteurPlaya

    if args.pages:
        pages = [max(1, int(x)) for x in args.pages.split(",")]
    else:
        pages = None
    if args.playa:
        conv: Union[Converteur, ConverteurPlaya] = ConverteurPlaya(args.pdf)
    else:
        conv = Converteur(args.pdf)
    words = conv.extract_words(pages)
    write_csv(words, sys.stdout)


def segment_main(args: argparse.Namespace):
    """Segmenter un CSV"""
    from .convert import write_csv
    from .segment import Segmenteur

    crf: Segmenteur
    crf = Segmenteur(args.model)
    reader = csv.DictReader(args.csv)
    write_csv(crf(reader), sys.stdout)


def label_main(args: argparse.Namespace):
    """Étiquetter un CSV"""
    from .convert import write_csv
    from .label import Identificateur

    crf = Identificateur(args.model)
    reader = csv.DictReader(args.csv)
    write_csv(crf(reader), sys.stdout)


def html_main(args: argparse.Namespace):
    """Convertir un CSV segmenté et étiquetté en HTML"""
    from .analyse import Analyseur, Bloc
    from .format import format_html

    reader = csv.DictReader(args.csv)
    analyseur = Analyseur(args.csv.name, reader)
    if args.images is not None:
        with open(args.images / "images.json", "rt") as infh:
            images = (Bloc(**image_dict) for image_dict in json.load(infh))
            analyseur.add_images(images, merge=False)
        doc = analyseur()
        print(format_html(doc, imgdir=args.images))
    else:
        doc = analyseur()
        print(format_html(doc))


def json_main(args: argparse.Namespace):
    """Convertir un CSV segmenté et étiquetté en JSON"""
    from .analyse import Analyseur, Bloc

    iob = csv.DictReader(args.csv)
    analyseur = Analyseur(args.csv.name, iob)
    if args.images:
        with open(args.images / "images.json", "rt") as infh:
            images = [Bloc(**image_dict) for image_dict in json.load(infh)]
            doc = analyseur(images)
    else:
        doc = analyseur()
    print(json.dumps(dataclasses.asdict(doc), indent=2, ensure_ascii=False))


def make_argparse() -> argparse.ArgumentParser:
    """Make the argparse"""
    from alexi.label import DEFAULT_MODEL as DEFAULT_LABEL_MODEL
    from alexi.segment import DEFAULT_MODEL as DEFAULT_SEGMENT_MODEL

    from . import annotate, download, extract, index, search

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-v", "--verbose", help="Émettre des messages", action="store_true"
    )
    subp = parser.add_subparsers(required=True)
    download_command = subp.add_parser(
        "download", help="Télécharger les documents plus récents du site web"
    )
    download.add_arguments(download_command)
    download_command.set_defaults(func=download.main)

    convert = subp.add_parser(
        "convert", help="Convertir le texte et les objets des fichiers PDF en CSV"
    )
    convert.add_argument(
        "pdf",
        help="Fichier PDF à traiter",
        type=Path,
    )
    convert.add_argument(
        "--pages", help="Liste de numéros de page à extraire, séparés par virgule"
    )
    convert.add_argument("--playa", help="Utiliser PLAYA", action="store_true")
    convert.set_defaults(func=convert_main)

    segment = subp.add_parser(
        "segment", help="Segmenter et étiquetter les segments d'un CSV"
    )
    segment.add_argument(
        "--model", help="Modele CRF", type=Path, default=DEFAULT_SEGMENT_MODEL
    )
    segment.add_argument(
        "csv",
        help="Fichier CSV à traiter",
        type=argparse.FileType("rt"),
    )
    segment.set_defaults(func=segment_main)

    label = subp.add_parser(
        "label", help="Étiquetter (extraire des informations) un CSV segmenté"
    )
    label.add_argument(
        "--model", help="Modele CRF", type=Path, default=DEFAULT_LABEL_MODEL
    )
    label.add_argument(
        "csv",
        help="Fichier CSV à traiter",
        type=argparse.FileType("rt"),
    )
    label.set_defaults(func=label_main)

    html = subp.add_parser(
        "html",
        help="Extraire la structure en format HTML en partant du CSV étiquetté",
    )
    html.add_argument("csv", help="Fichier CSV à traiter", type=argparse.FileType("rt"))
    html.add_argument(
        "--images", help="Répertoire avec des images des tableaux", type=Path
    )
    html.set_defaults(func=html_main)

    jsonf = subp.add_parser(
        "json",
        help="Extraire la structure en format JSON en partant du CSV étiquetté",
    )
    jsonf.add_argument(
        "csv", help="Fichier CSV à traiter", type=argparse.FileType("rt")
    )
    jsonf.add_argument(
        "--images", help="Répertoire contenant les images des tableaux", type=Path
    )
    jsonf.set_defaults(func=json_main)

    extract_command = subp.add_parser(
        "extract",
        help="Extraire la structure complète de fichiers PDF",
    )
    extract.add_arguments(extract_command)
    extract_command.set_defaults(func=extract.main)

    index_command = subp.add_parser(
        "index", help="Générer un index Whoosh sur les documents extraits"
    )
    index.add_arguments(index_command)
    index_command.set_defaults(func=index.main)

    search_command = subp.add_parser(
        "search", help="Effectuer une recherche sur l'index"
    )
    search.add_arguments(search_command)
    search_command.set_defaults(func=search.main)

    annotate_command = subp.add_parser(
        "annotate", help="Annoter un PDF pour corriger erreurs"
    )
    annotate.add_arguments(annotate_command)
    annotate_command.set_defaults(func=annotate.main)

    return parser


def main():
    parser = make_argparse()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(filename)s:%(lineno)d (%(funcName)s):%(levelname)s:%(message)s",
    )
    args.func(args)


if __name__ == "__main__":
    main()
