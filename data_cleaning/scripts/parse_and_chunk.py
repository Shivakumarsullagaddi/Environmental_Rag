#!/usr/bin/env python3
"""
Darukaa.Earth Pipeline - Phase 4 & 5: Document Parsing, Structural Cleaning & Semantic Chunking
Processes all 76 pages of the source PDF.
Performs structural cleaning (strips repeated headers/footers/page numbers/ligatures).
Creates semantic chunks across all sections for subsequent LLM review.
"""

import os
import sys
import json
import re
from datetime import datetime, timezone
import pymupdf
import tiktoken

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'pipeline_config.json')

with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRATCH_DIR = os.path.join(PROJECT_ROOT, 'scratch')
os.makedirs(SCRATCH_DIR, exist_ok=True)

PDF_PATH = os.path.join(SCRATCH_DIR, 'biodiversity.pdf')
RAW_CHUNKS_FILE = os.path.join(SCRATCH_DIR, 'raw_chunks.jsonl')

SOURCE_URI = f"gs://{config['source_bucket']}/{config['target_file']}"
BOOK_ID = config['target_book_id']
DOMAIN = config['domain']

TOC_MAP = {
    1: 'Global Processes', 2: 'Definition of Biodiversity', 3: 'Spatial Gradients in Biodiversity',
    4: 'Introduction to the Biodiversity Hierarchy', 5: 'What is Biodiversity? A comparison of spider communities',
    6: 'Species Diversity', 7: 'Alpha, Beta, and Gamma Diversity', 8: 'Introduction to Utilitarian Valuation of Biodiversity',
    9: 'Biodiversity over Time', 10: 'A Brief History of Life on Earth', 11: 'Ecosystem Diversity',
    12: 'Population Diversity', 13: 'Biogeographic Diversity', 14: 'Community Diversity',
    15: 'Ecoregions', 16: 'Extinction', 17: 'Landscape Diversity', 18: 'Ecological Value'
}

LIGATURE_MAP = {
    '\x15': '—', '\x1b': 'ff', '\x1c': 'fi', '\x1d': 'fl', '\x1e': 'ffi',
    '\x1f': 'ffl', '\x10': '"', '\x11': '"', '\x00': '(', '\x01': ')',
}

enc = tiktoken.get_encoding('cl100k_base')

def clean_text(text: str) -> str:
    for k, v in LIGATURE_MAP.items():
        text = text.replace(k, v)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def parse_all_pages(pdf_path: str):
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} not found.")
        sys.exit(1)

    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    print(f"Loaded PDF with {total_pages} pages from {pdf_path}")

    current_chap = 'Front Matter'
    current_sec = 'Title & Publication'

    extracted_blocks = []

    for p_idx in range(total_pages):
        page_no = p_idx + 1
        page = doc[p_idx]
        raw_blocks = page.get_text('blocks')

        i = 0
        while i < len(raw_blocks):
            b = raw_blocks[i]
            raw_text = b[4].strip()
            y0, y1 = b[1], b[3]

            # Structural cleaning:
            # 1. Strip running headers (top margin <= 88)
            if y0 <= 88 and (re.match(r'^\d+$', raw_text) or 'CHAPTER' in raw_text.upper() or 'GLOSSARY' in raw_text.upper() or 'BIBLIOGRAPHY' in raw_text.upper() or 'ATTRIBUTIONS' in raw_text.upper() or 'INDEX' in raw_text.upper()):
                i += 1
                continue

            # 2. Strip lone page numbers at bottom margin (>= 730)
            if y1 >= 730 and re.match(r'^\d+$', raw_text):
                i += 1
                continue

            # 3. Strip Connexions web link footnotes
            if 'This content is available online at' in raw_text:
                i += 1
                continue

            cleaned = clean_text(raw_text)
            if not cleaned or len(cleaned) < 3 or re.match(r'^\d+$', cleaned):
                i += 1
                continue

            # Section & Chapter Tracking
            if page_no == 5:
                current_chap = 'Front Matter'
                current_sec = 'Table of Contents'
            elif 'Chapter' in cleaned[:15]:
                m_ch = re.match(r'^Chapter\s+(\d+)', cleaned, re.IGNORECASE)
                if m_ch:
                    ch_num = int(m_ch.group(1))
                    current_chap = f'Chapter {ch_num}: {TOC_MAP.get(ch_num, "")}'
                    current_sec = 'Overview'
            elif 'GLOSSARY' in cleaned.upper() and page_no >= 61:
                current_chap = 'Glossary'
                current_sec = 'Definitions'
            elif 'BIBLIOGRAPHY' in cleaned.upper() and page_no >= 65:
                current_chap = 'Bibliography'
                current_sec = 'References'
            elif 'INDEX' in cleaned.upper() and page_no >= 72:
                current_chap = 'Index'
                current_sec = 'Index of Terms'
            elif 'ATTRIBUTIONS' in cleaned.upper() and page_no >= 73:
                current_chap = 'Attributions'
                current_sec = 'Licensing'
            elif page_no == 76:
                current_chap = 'Back Matter'
                current_sec = 'Summary'

            m_sec = re.match(r'^(\d+\.\d+)\s+([A-Z][^\n]+)', cleaned)
            if m_sec:
                current_sec = f"{m_sec.group(1)} {m_sec.group(2).strip()}"

            # If a block itself is very large (> 450 tokens), split it into smaller line-groups
            block_toks = len(enc.encode(cleaned))
            if block_toks > 450:
                lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
                sub_group = []
                sub_toks = 0
                for line in lines:
                    lt = len(enc.encode(line))
                    if sub_group and (sub_toks + lt > 400):
                        extracted_blocks.append({
                            'page': page_no,
                            'chapter': current_chap,
                            'section': current_sec,
                            'text': "\n".join(sub_group)
                        })
                        sub_group = []
                        sub_toks = 0
                    sub_group.append(line)
                    sub_toks += lt
                if sub_group:
                    extracted_blocks.append({
                        'page': page_no,
                        'chapter': current_chap,
                        'section': current_sec,
                        'text': "\n".join(sub_group)
                    })
            else:
                extracted_blocks.append({
                    'page': page_no,
                    'chapter': current_chap,
                    'section': current_sec,
                    'text': cleaned
                })
            i += 1

    print(f"Extracted {len(extracted_blocks)} cleaned blocks across all {total_pages} pages.")
    return extracted_blocks

def create_chunks(blocks, target_tokens=350, max_tokens=500, min_tokens=120):
    chunks = []
    current_group = []
    current_tokens = 0

    for b in blocks:
        tok_count = len(enc.encode(b['text']))
        if current_group:
            different_section = b['chapter'] != current_group[0]['chapter']
            exceeds_size = (current_tokens + tok_count) > max_tokens or (current_tokens >= target_tokens and tok_count > 100)
            if different_section or exceeds_size:
                c_text = "\n\n".join([x['text'] for x in current_group])
                c_toks = len(enc.encode(c_text))
                chunks.append({
                    'chapter': current_group[0]['chapter'],
                    'section': current_group[0]['section'],
                    'page_start': current_group[0]['page'],
                    'page_end': current_group[-1]['page'],
                    'content': c_text,
                    'tokens': c_toks
                })
                current_group = []
                current_tokens = 0

        current_group.append(b)
        current_tokens += tok_count

    if current_group:
        c_text = "\n\n".join([x['text'] for x in current_group])
        c_toks = len(enc.encode(c_text))
        chunks.append({
            'chapter': current_group[0]['chapter'],
            'section': current_group[0]['section'],
            'page_start': current_group[0]['page'],
            'page_end': current_group[-1]['page'],
            'content': c_text,
            'tokens': c_toks
        })

    now_ts = datetime.now(timezone.utc).isoformat()
    final_records = []
    for idx, c in enumerate(chunks, start=1):
        chunk_id = f"{BOOK_ID}_chunk_{idx:04d}"
        final_records.append({
            'chunk_id': chunk_id,
            'book_id': BOOK_ID,
            'source_type': 'book',
            'domain': DOMAIN,
            'chapter': c['chapter'],
            'section': c['section'],
            'page_start': c['page_start'],
            'page_end': c['page_end'],
            'content': c['content'],
            'token_count': c['tokens'],
            'source_uri': SOURCE_URI,
            'created_at': now_ts
        })

    return final_records

def main():
    blocks = parse_all_pages(PDF_PATH)
    raw_chunks = create_chunks(blocks)

    with open(RAW_CHUNKS_FILE, 'w', encoding='utf-8') as f:
        for c in raw_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + '\n')

    print(f"Generated {len(raw_chunks)} raw semantic chunks before LLM filtering.")
    tokens = [c['token_count'] for c in raw_chunks]
    print(f"Token statistics: min={min(tokens)}, max={max(tokens)}, avg={sum(tokens)/len(tokens):.1f}")
    print(f"Saved raw chunks to {RAW_CHUNKS_FILE}")

if __name__ == '__main__':
    main()
