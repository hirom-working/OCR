#!/usr/bin/env python3
"""PDF Text Extractor for Study Material Generation.

Extract text from OCR'd PDF, detect table of contents, and output structured JSON.

Usage:
    python extract_pdf_text.py <pdf_path> [--output <json_path>]
"""

import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF


def extract_page_text(page) -> str:
    """Extract text from a single page."""
    return page.get_text().strip()


def find_toc_page(doc, max_pages: int = 15) -> int | None:
    """Find the table of contents page.

    Returns the page number (0-indexed) or None if not found.
    """
    toc_keywords = [
        "目次", "もくじ", "目 次", "CONTENTS", "Contents", "contents",
        "TABLE OF CONTENTS", "Table of Contents"
    ]

    for page_num in range(min(max_pages, len(doc))):
        text = doc[page_num].get_text()
        # Check if this looks like a TOC page
        for kw in toc_keywords:
            if kw in text:
                # Additional check: TOC pages usually have many page numbers
                page_num_pattern = r'\b\d{1,3}\b'
                matches = re.findall(page_num_pattern, text)
                if len(matches) >= 3:  # At least 3 page numbers
                    return page_num
    return None


def parse_toc(doc, toc_page_num: int) -> list[dict]:
    """Parse table of contents and extract chapter information.

    Returns list of chapters with title and page number.
    """
    chapters = []
    text = doc[toc_page_num].get_text()

    # Common patterns for chapter entries
    # Pattern: "第1章 タイトル ... 15" or "Chapter 1 Title ... 15"
    patterns = [
        r'第\s*(\d+)\s*章[\.:\s]+(.+?)\s*[\.…\s]+(\d+)',
        r'Chapter\s+(\d+)[\.:\s]+(.+?)\s*[\.…\s]+(\d+)',
        r'(\d+)[\.:\s]+(.+?)\s*[\.…\s]+(\d+)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            for match in matches:
                chapters.append({
                    "number": int(match[0]),
                    "title": match[1].strip(),
                    "page": int(match[2])
                })
            break

    # If no pattern matched, try simpler line-by-line parsing
    if not chapters:
        lines = text.split('\n')
        for line in lines:
            # Look for lines ending with page numbers
            match = re.search(r'^(.+?)\s+(\d{1,3})\s*$', line.strip())
            if match:
                title = match.group(1).strip()
                page = int(match.group(2))
                # Filter out noise
                if len(title) > 2 and page > 0:
                    chapters.append({
                        "number": len(chapters) + 1,
                        "title": title,
                        "page": page
                    })

    return chapters


def extract_chapter_text(doc, start_page: int, end_page: int) -> str:
    """Extract text from a range of pages."""
    texts = []
    for page_num in range(start_page, min(end_page, len(doc))):
        text = extract_page_text(doc[page_num])
        if text:
            texts.append(f"--- Page {page_num + 1} ---\n{text}")
    return "\n\n".join(texts)


def extract_all_text(doc) -> str:
    """Extract text from entire document."""
    texts = []
    for page_num, page in enumerate(doc):
        text = extract_page_text(page)
        if text:
            texts.append(f"--- Page {page_num + 1} ---\n{text}")
    return "\n\n".join(texts)


def process_pdf(pdf_path: Path) -> dict:
    """Process PDF and extract structured content.

    Returns:
        {
            "title": str,
            "total_pages": int,
            "has_toc": bool,
            "chapters": [
                {
                    "number": int,
                    "title": str,
                    "start_page": int,
                    "end_page": int,
                    "text": str
                }
            ]
        }
    """
    doc = fitz.open(pdf_path)

    result = {
        "title": pdf_path.stem,
        "total_pages": len(doc),
        "has_toc": False,
        "chapters": []
    }

    # Try to find and parse TOC
    toc_page = find_toc_page(doc)

    if toc_page is not None:
        chapters = parse_toc(doc, toc_page)
        if chapters:
            result["has_toc"] = True

            # Calculate page ranges and extract text
            for i, chapter in enumerate(chapters):
                start_page = chapter["page"] - 1  # Convert to 0-indexed

                # End page is the start of next chapter or end of document
                if i + 1 < len(chapters):
                    end_page = chapters[i + 1]["page"] - 1
                else:
                    end_page = len(doc)

                result["chapters"].append({
                    "number": chapter["number"],
                    "title": chapter["title"],
                    "start_page": start_page + 1,  # Back to 1-indexed for display
                    "end_page": end_page,
                    "text": extract_chapter_text(doc, start_page, end_page)
                })

    # If no TOC found, treat entire document as one chapter
    if not result["chapters"]:
        result["chapters"].append({
            "number": 1,
            "title": result["title"],
            "start_page": 1,
            "end_page": len(doc),
            "text": extract_all_text(doc)
        })

    doc.close()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Extract text from PDF for study material generation"
    )
    parser.add_argument("pdf_path", type=Path, help="Path to the PDF file")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="Output JSON file path (default: stdout)"
    )
    parser.add_argument(
        "--pretty", action="store_true",
        help="Pretty print JSON output"
    )

    args = parser.parse_args()

    if not args.pdf_path.exists():
        print(f"Error: File not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    try:
        result = process_pdf(args.pdf_path)

        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2 if args.pretty else None)
            print(f"Output written to: {args.output}")
        else:
            indent = 2 if args.pretty else None
            print(json.dumps(result, ensure_ascii=False, indent=indent))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
