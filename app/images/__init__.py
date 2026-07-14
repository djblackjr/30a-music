"""
app/images/
Screenshot ingestion. Two importers, one entry point:

  - importer.py : GPT-4o Vision (needs OPENAI_API_KEY). Accurate — handles
                  multi-column weekly grids, stylised flyers, low-contrast text.
                  The only path CI can use.
  - ocr.py      : Apple Vision OCR (macOS only, no API key). Free and local, but
                  a much cruder parser. Fallback when no OpenAI key is set.

ingest_inbox() picks between them. Override with IMAGE_IMPORTER=vision|ocr.
"""
import logging
import os

logger = logging.getLogger(__name__)


def ingest_inbox(prefer: str | None = None) -> list[dict]:
    """
    Ingest images/inbox/ with the best available importer.

    auto (default): GPT-4o Vision when OPENAI_API_KEY is set, else Apple Vision
    OCR when it is available, else nothing (with a warning — images are left in
    the inbox rather than being consumed by an importer that cannot read them).
    """
    from app.images import ocr
    from app.images.importer import process_inbox as vision_process_inbox

    mode = (prefer or os.getenv("IMAGE_IMPORTER") or "auto").lower()
    has_key = bool(os.getenv("OPENAI_API_KEY"))

    if mode == "vision" or (mode == "auto" and has_key):
        logger.info("Image ingestion: GPT-4o Vision")
        return vision_process_inbox()

    if mode == "ocr" or (mode == "auto" and ocr.is_available()):
        if mode == "auto":
            logger.info("Image ingestion: no OPENAI_API_KEY — falling back to Apple Vision OCR")
        else:
            logger.info("Image ingestion: Apple Vision OCR")
        return ocr.process_inbox_ocr()

    logger.warning(
        "No image importer available. Set OPENAI_API_KEY, or install "
        "pyobjc-framework-Vision on macOS. Images left in the inbox."
    )
    return []
