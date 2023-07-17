"""Conversion de PDF en CSV"""

import logging
from typing import Any, Iterable, Iterator

import pdfplumber

LOGGER = logging.getLogger("convert")


def extract_words(pdf: pdfplumber.PDF) -> Iterator[dict[str, Any]]:
    for p in pdf.pages:
        words = p.extract_words()
        tables = p.find_tables()

        def in_table(w):
            for t in tables:
                x0, top, x1, bottom = t.bbox
                if (
                    w["x0"] < x1
                    and w["x1"] > x0
                    and w["top"] < bottom
                    and w["bottom"] > top
                ):
                    return t

        # Index characters for lookup
        chars = dict(((c["x0"], c["top"]), c) for c in p.chars)
        LOGGER.info("traitement de la page %d", p.page_number)
        prev_table = None
        for w in words:
            # Extract colour from first character (FIXME: assumes RGB space)
            if c := chars.get((w["x0"], w["top"])):
                # OMG WTF pdfplumber!!!
                if isinstance(c["stroking_color"], list) or isinstance(
                    c["stroking_color"], tuple
                ):
                    if len(c["stroking_color"]) == 1:
                        w["r"] = w["g"] = w["b"] = c["stroking_color"][0]
                    elif len(c["stroking_color"]) == 3:
                        w["r"], w["g"], w["b"] = c["stroking_color"]
                    else:
                        LOGGER.warning(
                            "Espace couleur non pris en charge: %s",
                            c["stroking_color"],
                        )
                else:
                    w["r"] = w["g"] = w["b"] = c["stroking_color"]
            w["page"] = p.page_number
            w["page_height"] = round(float(p.height))
            w["page_width"] = round(float(p.width))
            # Tag words in detected tables
            if table := in_table(w):
                if table != prev_table:
                    w["tag"] = "B-Tableau"
                else:
                    w["tag"] = "I-Tableau"
            prev_table = table

            # Round positions to points
            for field in "x0", "x1", "top", "bottom", "doctop":
                w[field] = round(float(w[field]))
            yield w


class Converteur:
    def __call__(self, infh: Any) -> Iterable[dict[str, Any]]:
        with pdfplumber.open(infh) as pdf:
            return extract_words(pdf)