"""
Configure and call local Tesseract OCR.

Tesseract is a separate Windows program (not a Python package). Python talks to
it through `pytesseract`, which shells out to `tesseract.exe`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT_WINDOWS_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def resolve_tesseract_cmd() -> str | None:
    """Find tesseract.exe: env override → PATH → common Windows install."""
    env_cmd = (os.getenv("TESSERACT_CMD") or "").strip().strip('"')
    if env_cmd and Path(env_cmd).is_file():
        return str(Path(env_cmd))

    which = shutil.which("tesseract")
    if which:
        return which

    if DEFAULT_WINDOWS_TESSERACT.is_file():
        return str(DEFAULT_WINDOWS_TESSERACT)

    return None


def configure_tesseract() -> str:
    """
    Point pytesseract at the installed binary and return its path.

    Raises RuntimeError if Tesseract is not installed / not findable.
    """
    import pytesseract

    cmd = resolve_tesseract_cmd()
    if not cmd:
        raise RuntimeError(
            "Tesseract not found. Install it or set TESSERACT_CMD in .env "
            r"(e.g. C:\Program Files\Tesseract-OCR\tesseract.exe)."
        )

    pytesseract.pytesseract.tesseract_cmd = cmd

    tessdata = (os.getenv("TESSDATA_PREFIX") or "").strip().strip('"')
    if tessdata:
        os.environ["TESSDATA_PREFIX"] = tessdata

    return cmd


def ocr_image(path: Path | str, *, lang: str = "eng") -> str:
    """Run OCR on a single image file and return plain text."""
    from PIL import Image
    import pytesseract

    configure_tesseract()
    with Image.open(path) as img:
        return (pytesseract.image_to_string(img, lang=lang) or "").strip()


def tesseract_status() -> dict[str, object]:
    """Quick health check for CLI / debugging."""
    import pytesseract

    cmd = configure_tesseract()
    version = str(pytesseract.get_tesseract_version())
    langs = pytesseract.get_languages(config="")
    return {"tesseract_cmd": cmd, "version": version, "languages": langs}
