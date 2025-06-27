"""Analyse de mise en page avec modèle RT-DETR de IBM (Docling)"""

import logging
from os import PathLike, cpu_count
from pathlib import Path
from typing import Iterable, Iterator, Union

import paves.image as pi
import playa
from docling_ibm_models.layoutmodel.layout_predictor import LayoutPredictor

from alexi.analyse import Bloc
from alexi.recognize import Objets

LOGGER = logging.getLogger(Path(__file__).stem)


def scale_to_model(page: playa.Page, modeldim: float):
    """Find scaling factor for model dimension."""
    mindim = min(page.width, page.height)
    return modeldim / mindim


def load_model_from_hub() -> Path:
    from huggingface_hub import hf_hub_download  # type: ignore[import-untyped]

    hf_hub_download(
        "ds4sd/docling-models", "model_artifacts/layout/preprocessor_config.json"
    )
    hf_hub_download("ds4sd/docling-models", "model_artifacts/layout/config.json")
    weights_path = hf_hub_download(
        "ds4sd/docling-models", "model_artifacts/layout/model.safetensors"
    )
    return Path(weights_path).parent


class convert_page:
    """Not a partial function, but a partial function."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height

    def __call__(self, page: playa.Page):
        scale_x = page.width / self.width
        scale_y = page.height / self.height

        return (
            page.page_idx + 1,
            scale_x,
            scale_y,
            next(pi.convert(page, width=self.width, height=self.height)),
        )


class ObjetsDocling(Objets):
    """Détecteur d'objects textuels utilisant RT-DETR."""

    def __init__(
        self,
        model_path: Union[str, PathLike, None] = None,
        torch_device: str = "cpu",
        num_threads: int = 4,
        base_threshold: float = 0.3,
    ) -> None:
        if model_path is None:
            model_path = load_model_from_hub()
        else:
            model_path = Path(model_path)
        self.model = LayoutPredictor(
            str(model_path),
            device=torch_device,
            num_threads=num_threads,
            base_threshold=base_threshold,
        )
        self.model_info = self.model.info()

    def __call__(
        self,
        pdf_path: Union[str, PathLike],
        pages: Union[None, Iterable[int]] = None,
        labelmap: Union[dict, None] = None,
    ) -> Iterator[Bloc]:
        ncpu = cpu_count()
        ncpu = 1 if ncpu is None else round(ncpu / 2)
        with playa.open(pdf_path, max_workers=ncpu) as pdf:
            pdfpages = pdf.pages if pages is None else pdf.pages[pages]
            modeldim = self.model_info["image_size"]
            for page_number, scale_x, scale_y, image in pdfpages.map(
                convert_page(width=modeldim, height=modeldim)
            ):

                def boxsort(box):
                    """Sort by topmost-leftmost-tallest-widest."""
                    return (
                        box["t"],
                        box["l"],
                        -(box["b"] - box["t"]),
                        -(box["r"] - box["l"]),
                    )

                boxes = sorted(self.model.predict(image), key=boxsort)
                for box in boxes:
                    if labelmap is None or box["label"] in labelmap:
                        bbox = (
                            round(box["l"] * scale_x),
                            round(box["t"] * scale_y),
                            round(box["r"] * scale_x),
                            round(box["b"] * scale_y),
                        )
                        yield Bloc(
                            type=(
                                box["label"]
                                if labelmap is None
                                else labelmap[box["label"]]
                            ),
                            contenu=[],
                            _page_number=page_number,
                            _bbox=bbox,
                        )


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()

    recx = ObjetsDocling()
    for bloc in recx(args.pdf):
        print(bloc)


if __name__ == "__main__":
    main()
