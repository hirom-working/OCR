"""YOMITOKU OCR API Server"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Optional

import cv2
import numpy as np
import pypdfium2 as pdfium
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from yomitoku import DocumentAnalyzer

app = FastAPI(title="YOMITOKU OCR API", version="0.1.0")

analyzer: Optional[DocumentAnalyzer] = None
executor: Optional[ThreadPoolExecutor] = None


class Paragraph(BaseModel):
    contents: str
    box: list[float]
    direction: str
    order: int
    role: Optional[str] = None


class PageResult(BaseModel):
    page_number: int
    paragraphs: list[Paragraph]
    full_text: str


class OCRResponse(BaseModel):
    pages: list[PageResult]
    total_pages: int
    full_text: str


@app.on_event("startup")
async def startup():
    global analyzer, executor
    executor = ThreadPoolExecutor(max_workers=2)
    analyzer = DocumentAnalyzer(device="cuda")


@app.on_event("shutdown")
async def shutdown():
    global executor
    if executor:
        executor.shutdown(wait=True)
        executor = None


@app.get("/health")
async def health():
    return {"status": "healthy", "model_loaded": analyzer is not None}


def _process_image_sync(img: np.ndarray) -> PageResult:
    """Synchronous image processing (runs in thread pool)."""
    results, _, _ = analyzer(img)

    paragraphs = []
    for p in results.paragraphs:
        paragraphs.append(
            Paragraph(
                contents=p.contents,
                box=list(p.box),
                direction=p.direction,
                order=p.order,
                role=p.role,
            )
        )

    full_text = "\n".join(
        p.contents for p in sorted(paragraphs, key=lambda x: x.order)
    )

    return PageResult(page_number=1, paragraphs=paragraphs, full_text=full_text)


def pdf_to_images(pdf_bytes: bytes, scale: float = 2.0) -> list[np.ndarray]:
    """Convert PDF to list of images."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    images = []
    for page in pdf:
        bitmap = page.render(scale=scale)
        img = bitmap.to_numpy()
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        images.append(img)
    return images


@app.post("/ocr", response_model=OCRResponse)
async def ocr(file: UploadFile = File(...)):
    """
    Perform OCR on uploaded PDF or image file.

    Supported formats: PDF, PNG, JPG, JPEG
    """
    if analyzer is None or executor is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    content = await file.read()
    filename = file.filename.lower() if file.filename else ""

    try:
        if filename.endswith(".pdf") or file.content_type == "application/pdf":
            images = pdf_to_images(content)
        else:
            nparr = np.frombuffer(content, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                raise HTTPException(status_code=400, detail="Invalid image file")
            images = [img]

        loop = asyncio.get_event_loop()
        pages = []
        all_text = []

        for i, img in enumerate(images):
            result = await loop.run_in_executor(
                executor, partial(_process_image_sync, img)
            )
            result.page_number = i + 1
            pages.append(result)
            all_text.append(result.full_text)

        return OCRResponse(
            pages=pages, total_pages=len(pages), full_text="\n\n".join(all_text)
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"OCR processing failed: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
