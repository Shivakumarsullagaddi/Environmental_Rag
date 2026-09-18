#!/usr/bin/env python3
import pymupdf

papers = ['2.pdf', '5.pdf', '7.pdf', '10.pdf', '11.pdf']
for p in papers:
    path = f'scratch/{p}'
    doc = pymupdf.open(path)
    total_chars = sum(len(page.get_text().strip()) for page in doc)
    print(f"=== {p} ===")
    print(f"  Pages: {len(doc)}, Total Text Chars: {total_chars}")
    print(f"  Page 1 preview: {repr(doc[0].get_text()[:250])}")
