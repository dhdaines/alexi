from pathlib import Path

from alexi.extract import Extracteur
from alexi.index import index
from alexi.search import search

DATADIR = Path(__file__).parent / "data"


def test_index(tmp_path: Path, capsys):
    docdir = tmp_path / "st_clac_clac"
    outdir = tmp_path / "_idx"
    extracteur = Extracteur(docdir)
    doc = extracteur(DATADIR / "zonage_zones.pdf")
    extracteur.output_doctree([doc])
    extracteur.output_html(doc)
    extracteur.output_json()
    index([docdir], outdir)
    assert (outdir / "index.json").exists()
    assert (outdir / "textes.json").exists()
    search(outdir, docdir, ["foret"], 10)
    assert "FORÊT" in capsys.readouterr().out
