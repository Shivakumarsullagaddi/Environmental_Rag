#!/usr/bin/env python3
import pymupdf
import re

SECTION_PATTERNS = [
    (r'^(?:abstract|summary|highlights)\b', 'Abstract'),
    (r'^(?:significance)\b', 'Significance'),
    (r'^(?:introduction|background)\b', 'Introduction'),
    (r'^(?:results\s+and\s+discussion)\b', 'Results and Discussion'),
    (r'^(?:results)\b', 'Results'),
    (r'^(?:discussion)\b', 'Discussion'),
    (r'^(?:limitations(?:\s+of\s+the\s+study)?)\b', 'Limitations'),
    (r'^(?:conclusions?)\b', 'Conclusion'),
    (r'^(?:materials?\s+and\s+methods?|star\s+methods|methods)\b', 'Methods'),
    (r'^(?:data\s+availability|code\s+availability)\b', 'Data Availability'),
    (r'^(?:acknowledgements?|funding)\b', 'Acknowledgments'),
    (r'^(?:author\s+contributions?|credit\s+authorship)\b', 'Author Contributions'),
    (r'^(?:declaration\s+of\s+competing\s+interest|competing\s+interests?)\b', 'Competing Interests'),
    (r'^(?:references|literature\s+cited|bibliography)\b', 'References')
]

for p in ['2.pdf', '5.pdf', '7.pdf', '10.pdf', '11.pdf']:
    path = f'scratch/{p}'
    doc = pymupdf.open(path)
    sections_found = set()
    for page in doc:
        blocks = page.get_text('blocks')
        for b in blocks:
            t = b[4].strip()
            first_line = t.split('\n')[0].strip()
            # Check numbered or unnumbered headings e.g. "1. Introduction", "2. Methods"
            m = re.match(r'^(?:\d+\.?\s+)?([A-Za-z\s–—\-\:]+)$', first_line)
            if m and len(first_line) < 60:
                h = m.group(1).strip()
                for pat, canon in SECTION_PATTERNS:
                    if re.search(pat, h, re.I):
                        sections_found.add(first_line)
    print(f"=== {p} Detected Sections ===")
    for s in sorted(sections_found):
        print(f"  {s}")
