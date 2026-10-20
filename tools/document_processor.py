from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from tools.ocr_engine import PaddleOCREngine


@dataclass
class PageResult:
    page_number: int
    text: str
    extraction_method: str
    ocr_confidence: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProcessedDocument:
    file_name: str
    pages: list[PageResult]
    warnings: list[str] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        return "\n\n".join(
            f"[Page {page.page_number}]\n{page.text}"
            for page in self.pages
        )

    @property
    def page_count(self) -> int:
        return len(self.pages)


class DocumentProcessor:
    """Extract embedded PDF text and OCR pages with insufficient text."""

    def __init__(
        self,
        ocr_engine: PaddleOCREngine | None = None,
        min_text_chars: int = 30,
        max_image_dim: int = 2000,
        render_scale: float = 2.0,
    ) -> None:
        if min_text_chars < 1:
            raise ValueError("min_text_chars must be positive.")
        if max_image_dim < 100:
            raise ValueError("max_image_dim must be at least 100.")
        if render_scale <= 0:
            raise ValueError("render_scale must be positive.")

        self.ocr_engine = ocr_engine or PaddleOCREngine()
        self.min_text_chars = min_text_chars
        self.max_image_dim = max_image_dim
        self.render_scale = render_scale

    def _has_sufficient_embedded_text(self, text: str) -> bool:
        """Accept searchable financial content, not caption-only text."""
        import re

        normalized = " ".join(text.split())

        if len(normalized) < self.min_text_chars:
            return False

        # Short searchable documents can still be valid if they contain
        # actual financial fields.
        financial_patterns = [
            r"\binvoice\s*(?:number|no\.?|id)\s*:",
            r"\binvoice\s+date\s*:",
            r"\bdue\s+date\s*:",
            r"\btotal\s+due\s*:",
            r"\bgrand\s+total\s*:",
            r"\bsubtotal\s*:",
            r"\bvendor\s*:",
            r"\bsupplier\s*:",
            r"\bamount\s+received\s*:",
            r"\bpayment\s+date\s*:",
            r"\breceived\s+from\s*:",
            r"\bopening\s+balance\s*:",
            r"\bclosing\s+balance\s*:",
        ]

        matched_fields = sum(
            bool(re.search(pattern, normalized, re.IGNORECASE))
            for pattern in financial_patterns
        )

        return matched_fields >= 2

    def process(self, file_path: str | Path) -> ProcessedDocument:
        path = Path(file_path)

        if not path.is_file():
            raise FileNotFoundError(f"Document not found: {path}")

        if path.suffix.lower() != ".pdf":
            raise ValueError("Only PDF files are supported by this processor.")

        pages: list[PageResult] = []

        with pymupdf.open(path) as pdf:
            if pdf.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported.")

            if len(pdf) == 0:
                raise ValueError("The PDF contains no pages.")

            for index, page in enumerate(pdf):
                embedded_text = page.get_text("text").strip()

                # Short embedded text may consist only of page headings,
                # captions, or footers surrounding a scanned document.
                if self._has_sufficient_embedded_text(embedded_text):
                    pages.append(
                        PageResult(
                            page_number=index + 1,
                            text=embedded_text,
                            extraction_method="embedded_text",
                        )
                    )
                    continue

                scale = self.render_scale
                rect = page.rect
                longest_side = max(rect.width, rect.height)

                if longest_side > 0:
                    scale = min(
                        scale,
                        self.max_image_dim / longest_side,
                    )

                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale),
                    alpha=False,
                )

                image_bytes = pix.tobytes("png")
                import io

                import PIL.Image

                with PIL.Image.open(io.BytesIO(image_bytes)) as image:
                    import tempfile

                    with tempfile.NamedTemporaryFile(
                        suffix=".png", delete=False
                    ) as temp:
                        temp_path = Path(temp.name)
                        image.save(temp_path, format="PNG")

                try:
                    ocr_result = self.ocr_engine.extract_text(temp_path)
                finally:
                    temp_path.unlink(missing_ok=True)

                ocr_text = ocr_result["text"].strip()
                warnings: list[str] = []

                if not ocr_text:
                    warnings.append("PaddleOCR found no readable text.")

                pages.append(
                    PageResult(
                        page_number=index + 1,
                        text=ocr_text,
                        extraction_method="paddleocr",
                        ocr_confidence=ocr_result["average_confidence"],
                        warnings=warnings,
                    )
                )

        return ProcessedDocument(
            file_name=path.name,
            pages=pages,
        )
