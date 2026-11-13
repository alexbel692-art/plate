"""Скачивает модели распознавания в ./models, чтобы положить их в установщик."""
import pathlib

from fast_plate_ocr.inference import hub as fpo
from open_image_models.detection.core import hub as oim

ROOT = pathlib.Path(__file__).resolve().parent.parent / "models"
for name in ("yolo-v9-t-640-license-plate-end2end", "yolo-v9-t-384-license-plate-end2end"):
    print(oim.download_model(name, save_directory=ROOT / "open-image-models" / name))
for name in ("cct-s-v2-global-model", "cct-xs-v2-global-model"):
    print(fpo.download_model(name, save_directory=ROOT / "fast-plate-ocr" / name))
