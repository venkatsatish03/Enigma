from __future__ import annotations

from pathlib import Path
from typing import Any


class PaddleOCREngine:
    """OCR engine using PaddleOCR for financial document images."""

    def __init__(self, language: str = "en") -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR is not installed or available. Please install paddleocr and paddlepaddle."
            ) from exc

        self.ocr = PaddleOCR(
            lang=language,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def extract_text(self, image_path: str | Path) -> dict[str, Any]:
        """Extract text, bounding boxes, and confidence scores from an image."""
        path = Path(image_path)

        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {path}")

        results = self.ocr.predict(input=str(path))

        lines: list[dict[str, Any]] = []

        for result in results:
            data = result.json
            if callable(data):
                data = data()

            if isinstance(data, dict):
                payload = data.get("res", data)
                texts = payload.get("rec_texts", [])
                scores = payload.get("rec_scores", [])
                boxes = payload.get("rec_polys", payload.get("dt_polys", []))

                for index, text in enumerate(texts):
                    score = scores[index] if index < len(scores) else None
                    box = boxes[index] if index < len(boxes) else None

                    lines.append({
                        "text": str(text),
                        "confidence": float(score) if score is not None else None,
                        "bounding_box": box.tolist() if hasattr(box, "tolist") else box,
                    })

        combined_text = "\n".join(
            item["text"] for item in lines if item["text"].strip()
        )

        confidences = [
            item["confidence"]
            for item in lines
            if item["confidence"] is not None
        ]

        return {
            "source_file": path.name,
            "text": combined_text,
            "lines": lines,
            "average_confidence": (
                sum(confidences) / len(confidences) if confidences else None
            ),
        }
