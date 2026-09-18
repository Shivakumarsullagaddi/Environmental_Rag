#!/usr/bin/env python3
"""
Darukaa.Earth Pipeline - Universal Book Processor
Performs:
1. Structural Cleaning & Semantic Chunking (250-500 tokens)
2. Gemini 2.5 Flash Knowledge Filtering (Strict scientific relevance)
3. Upload approved chunks to US Cloud Storage
4. Ingestion into BigQuery darukaa_kb.book_chunks & embedding generation in darukaa_kb.book_embeddings
5. Validation and Reporting
"""

import os
import sys
import json
import re
import time
import argparse
from datetime import datetime, timezone
import pymupdf
import tiktoken
from google import genai
from google.genai import types
from google.cloud import storage, bigquery

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'pipeline_config.json')
with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

PROJECT_ID = config['project_id']
PROCESSED_BUCKET = config['processed_bucket']
BQ_DATASET = config['bigquery_dataset']
BQ_LOCATION = config['bigquery_location']
EMBEDDING_MODEL = config['embedding_model']
GEMINI_MODEL = config['gemini_model']

enc = tiktoken.get_encoding('cl100k_base')

REGIONS = ['us-east4', 'us-central1', 'us-west1']
clients = {
    loc: genai.Client(vertexai=True, project=PROJECT_ID, location=loc)
    for loc in REGIONS
}

ALLOWED_TYPES = {
    "concept", "definition", "method", "finding",
    "discussion", "recommendation", "table", "figure", "other", "boilerplate"
}

LIGATURE_MAP = {
    '\x15': '—', '\x1b': 'ff', '\x1c': 'fi', '\x1d': 'fl', '\x1e': 'ffi',
    '\x1f': 'ffl', '\x10': '"', '\x11': '"', '\x00': '(', '\x01': ')',
}

def clean_text(text: str) -> str:
    for k, v in LIGATURE_MAP.items():
        text = text.replace(k, v)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def ocr_pdf_pages(doc, book_id):
    scratch_dir = os.path.join(os.path.dirname(__file__), '..', 'scratch')
    os.makedirs(scratch_dir, exist_ok=True)
    cache_path = os.path.join(scratch_dir, f"{book_id}_ocr_cache.json")
    if os.path.exists(cache_path):
        print(f"Loading OCR results from cache: {cache_path}")
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    ocr_pages = []
    total = len(doc)
    prompt = ("You are an expert OCR and document digitization system. "
              "Transcribe all text from this book page accurately. "
              "Preserve headings, section numbers, paragraph breaks, formulas, and lists. "
              "Do NOT include running page headers or standalone page numbers. "
              "Do NOT invent or summarize text. Only transcribe what is on the page.")
    
    for idx in range(total):
        page = doc[idx]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes('png')
        
        success = False
        for attempt in range(4):
            loc = REGIONS[(idx + attempt) % len(REGIONS)]
            client = clients[loc]
            try:
                resp = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[
                        types.Part.from_bytes(data=img_bytes, mime_type='image/png'),
                        prompt
                    ]
                )
                text = resp.text.strip() if resp.text else ""
                ocr_pages.append(text)
                success = True
                print(f"OCR page {idx+1}/{total} completed ({len(text)} chars)")
                break
            except Exception as e:
                print(f"OCR page {idx+1} attempt {attempt+1} failed: {e}")
                time.sleep(2.0 * (attempt + 1))
        
        if not success:
            ocr_pages.append("")
        time.sleep(0.5)
        
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(ocr_pages, f, ensure_ascii=False, indent=2)
    return ocr_pages

def parse_and_chunk_pdf(pdf_path, book_id, domain, source_uri):
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    
    extracted_blocks = []
    current_chapter = domain.replace('_', ' ').title()
    current_section = 'General'

    total_digital_text = sum(len(doc[p].get_text().strip()) for p in range(total_pages))
    if total_digital_text < 100:
        print(f"Detected scanned PDF ({total_digital_text} chars text). Running OCR with Gemini 2.5 Flash...")
        ocr_pages = ocr_pdf_pages(doc, book_id)
        for p_idx, page_text in enumerate(ocr_pages):
            page_no = p_idx + 1
            paragraphs = [p.strip() for p in page_text.split('\n\n') if p.strip()]
            for p in paragraphs:
                cleaned = clean_text(p)
                if not cleaned or len(cleaned) < 4:
                    continue
                first_line = cleaned.split('\n')[0].strip()
                if re.match(r'^(?:references|bibliography)\b', first_line, re.IGNORECASE):
                    current_chapter = 'Bibliography'
                    current_section = 'References'
                elif re.match(r'^(?:table of contents|contents|structure)\b', first_line, re.IGNORECASE):
                    current_chapter = 'Front Matter'
                    current_section = 'Structure / Contents'
                elif re.match(r'^(?:index)\b', first_line, re.IGNORECASE):
                    current_chapter = 'Index'
                    current_section = 'Index'
                elif re.match(r'^(?:unit\s+\d+|chapter\s+\d+)\b', first_line, re.IGNORECASE):
                    current_chapter = first_line
                else:
                    m_sec = re.match(r'^(\d+\.\d+(?:\.\d+)?\.?)\s+([A-Z][^\n]+)', first_line)
                    if m_sec:
                        current_section = f"{m_sec.group(1)} {m_sec.group(2).strip()}"
                
                block_toks = len(enc.encode(cleaned))
                if block_toks > 480:
                    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
                    sub_group = []
                    sub_toks = 0
                    for line in lines:
                        lt = len(enc.encode(line))
                        if sub_group and (sub_toks + lt > 400):
                            extracted_blocks.append({
                                'page': page_no,
                                'chapter': current_chapter,
                                'section': current_section,
                                'text': "\n".join(sub_group)
                            })
                            sub_group = []
                            sub_toks = 0
                        sub_group.append(line)
                        sub_toks += lt
                    if sub_group:
                        extracted_blocks.append({
                            'page': page_no,
                            'chapter': current_chapter,
                            'section': current_section,
                            'text': "\n".join(sub_group)
                        })
                else:
                    extracted_blocks.append({
                        'page': page_no,
                        'chapter': current_chapter,
                        'section': current_section,
                        'text': cleaned
                    })
    else:
        for p_idx in range(total_pages):
            page_no = p_idx + 1
            page = doc[p_idx]
            raw_blocks = page.get_text('blocks')
            
            for b in raw_blocks:
                raw_text = b[4].strip()
                y0, y1 = b[1], b[3]

                # Structural cleaning:
                # Strip headers at top margin (y0 <= 60 or 70 depending on geometry)
                if y0 <= 55 and (re.match(r'^\d+$', raw_text) or len(raw_text) < 40):
                    continue
                # Strip bottom margin lone numbers
                if y1 >= page.rect.height - 40 and re.match(r'^\d+$', raw_text):
                    continue
                if 'This content is available online at' in raw_text:
                    continue

                cleaned = clean_text(raw_text)
                if not cleaned or len(cleaned) < 4 or re.match(r'^\d+$', cleaned):
                    continue

                # Detect chapter / section headings
                # Match patterns like: "Chapter 1", "1.1 Introduction", "References", "Bibliography"
                if re.match(r'^(?:references|bibliography)\b', cleaned, re.IGNORECASE):
                    current_chapter = 'Bibliography'
                    current_section = 'References'
                elif re.match(r'^(?:table of contents|contents)\b', cleaned, re.IGNORECASE):
                    current_chapter = 'Front Matter'
                    current_section = 'Table of Contents'
                elif re.match(r'^(?:index)\b', cleaned, re.IGNORECASE):
                    current_chapter = 'Index'
                    current_section = 'Index'
                else:
                    m_sec = re.match(r'^(\d+\.\d+\.?)\s+([A-Z][^\n]+)', cleaned)
                    if m_sec:
                        current_section = f"{m_sec.group(1)} {m_sec.group(2).strip()}"

                # Split extra-large blocks (> 500 tokens)
                block_toks = len(enc.encode(cleaned))
                if block_toks > 480:
                    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
                    sub_group = []
                    sub_toks = 0
                    for line in lines:
                        lt = len(enc.encode(line))
                        if sub_group and (sub_toks + lt > 400):
                            extracted_blocks.append({
                                'page': page_no,
                                'chapter': current_chapter,
                                'section': current_section,
                                'text': "\n".join(sub_group)
                            })
                            sub_group = []
                            sub_toks = 0
                        sub_group.append(line)
                        sub_toks += lt
                    if sub_group:
                        extracted_blocks.append({
                            'page': page_no,
                            'chapter': current_chapter,
                            'section': current_section,
                            'text': "\n".join(sub_group)
                        })
                else:
                    extracted_blocks.append({
                        'page': page_no,
                        'chapter': current_chapter,
                        'section': current_section,
                        'text': cleaned
                    })

    # Group into semantic chunks (250 - 500 tokens)
    chunks = []
    current_group = []
    current_tokens = 0

    for b in extracted_blocks:
        tok_count = len(enc.encode(b['text']))
        if current_group:
            diff_chap = b['chapter'] != current_group[0]['chapter']
            exceeds = (current_tokens + tok_count) > 500 or (current_tokens >= 350 and tok_count > 100)
            if diff_chap or exceeds:
                c_text = "\n\n".join([x['text'] for x in current_group])
                chunks.append({
                    'chapter': current_group[0]['chapter'],
                    'section': current_group[0]['section'],
                    'page_start': current_group[0]['page'],
                    'page_end': current_group[-1]['page'],
                    'content': c_text,
                    'tokens': len(enc.encode(c_text))
                })
                current_group = []
                current_tokens = 0
        current_group.append(b)
        current_tokens += tok_count

    if current_group:
        c_text = "\n\n".join([x['text'] for x in current_group])
        chunks.append({
            'chapter': current_group[0]['chapter'],
            'section': current_group[0]['section'],
            'page_start': current_group[0]['page'],
            'page_end': current_group[-1]['page'],
            'content': c_text,
            'tokens': len(enc.encode(c_text))
        })

    now_ts = datetime.now(timezone.utc).isoformat()
    raw_records = []
    for idx, c in enumerate(chunks, start=1):
        raw_records.append({
            'chunk_id': f"{book_id}_chunk_{idx:04d}",
            'book_id': book_id,
            'source_type': 'book',
            'domain': domain,
            'chapter': c['chapter'],
            'section': c['section'],
            'page_start': c['page_start'],
            'page_end': c['page_end'],
            'content': c['content'],
            'token_count': c['tokens'],
            'source_uri': source_uri,
            'created_at': now_ts
        })
    return total_pages, raw_records

BATCH_PROMPT = """You are the Knowledge Filter for the Darukaa.Earth Environmental RAG system.
Evaluate each chunk extracted from an environmental science textbook to decide whether it should enter the clean knowledge base.

FILTERING RULES:
1. KEEP (keep: true):
   - Fundamental scientific concepts, definitions, scientific explanations, environmental relationships.
   - Scientific methods, sampling procedures, remote sensing techniques, taxonomic classification, quantitative formulas/metrics.
   - Empirical findings, ecosystem dynamics, case studies, agricultural management information.
   - Structured scientific data tables and informative figure captions.

2. DISCARD (keep: false):
   - Front matter: title/cover pages, publisher info, copyright notices, author biographies, ISBN.
   - Table of Contents and navigation-only text.
   - Bibliographies, literature references, and citation lists (e.g. "[1] Brown et al...", journal citations).
   - Index of terms, keywords, and page numbers.
   - Module attributions, collection licensing, Creative Commons boilerplate, and URL metadata.
   - Classroom activity logistics (e.g. cutting paper cards, student group assignments) without scientific knowledge.
   - Obvious noise, empty fragments, or promotional summaries.

CRITICAL:
- You MUST NOT edit, summarize, or rewrite the original chunk text.
- Return a JSON list of objects, one for each chunk in the input list:
[
  {{
    "chunk_id": "string matching given CHUNK_ID",
    "keep": boolean,
    "content_type": "concept" | "definition" | "method" | "finding" | "discussion" | "recommendation" | "table" | "figure" | "other" | "boilerplate",
    "reason": "Clear concise scientific explanation for decision",
    "confidence": float between 0.0 and 1.0,
    "topics": ["scientific_topic1", "scientific_topic2"]
  }}
]

Chunks to evaluate:
{chunks_payload}
"""

def review_chunks_with_llm(chunks, batch_size=4):
    batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    reviewed = []
    
    for b_idx, batch in enumerate(batches, start=1):
        chunks_text = []
        for c in batch:
            chunks_text.append(f"---\nCHUNK_ID: {c['chunk_id']}\nCHAPTER: {c.get('chapter', '')}\nSECTION: {c.get('section', '')}\nCONTENT:\n{c.get('content', '')}")
        payload = "\n".join(chunks_text)
        prompt = BATCH_PROMPT.format(chunks_payload=payload)
        
        max_retries = 4
        success = False
        for attempt in range(max_retries):
            loc = REGIONS[(b_idx + attempt) % len(REGIONS)]
            client = clients[loc]
            try:
                resp = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type='application/json',
                        temperature=0.0
                    )
                )
                data = json.loads(resp.text)
                results_map = {}
                if isinstance(data, list):
                    for item in data:
                        cid = item.get('chunk_id')
                        if cid:
                            ctype = item.get('content_type', 'other').lower()
                            if ctype not in ALLOWED_TYPES:
                                ctype = 'other'
                            results_map[cid] = {
                                'keep': bool(item.get('keep', False)),
                                'content_type': ctype,
                                'review_reason': item.get('reason', ''),
                                'review_confidence': float(item.get('confidence', 0.9)),
                                'topics': list(item.get('topics', []))
                            }
                for c in batch:
                    if c['chunk_id'] in results_map:
                        reviewed.append({**c, **results_map[c['chunk_id']]})
                    else:
                        reviewed.append({
                            **c,
                            'keep': False,
                            'content_type': 'boilerplate',
                            'review_reason': 'Unmatched chunk in response',
                            'review_confidence': 0.5,
                            'topics': []
                        })
                success = True
                break
            except Exception as e:
                time.sleep(2.0 * (attempt + 1))
        
        if not success:
            for c in batch:
                reviewed.append({
                    **c,
                    'keep': False,
                    'content_type': 'boilerplate',
                    'review_reason': 'Review failed',
                    'review_confidence': 0.0,
                    'topics': []
                })
        time.sleep(1.0)
        
    return reviewed

def save_and_ingest(book_id, domain, reviewed_chunks):
    scratch_dir = os.path.join(os.path.dirname(__file__), '..', 'scratch')
    os.makedirs(scratch_dir, exist_ok=True)
    
    approved = [c for c in reviewed_chunks if c['keep']]
    approved_file = os.path.join(scratch_dir, f"{book_id}_approved.jsonl")
    
    with open(approved_file, 'w', encoding='utf-8') as f:
        for c in approved:
            rec = {
                'chunk_id': c['chunk_id'],
                'book_id': c['book_id'],
                'source_type': c['source_type'],
                'domain': c['domain'],
                'chapter': c['chapter'],
                'section': c['section'],
                'page_start': c['page_start'],
                'page_end': c['page_end'],
                'content': c['content'],
                'content_type': c['content_type'],
                'topics': c['topics'],
                'review_confidence': c['review_confidence'],
                'source_uri': c['source_uri'],
                'created_at': c['created_at']
            }
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            
    # Upload to GCS
    storage_client = storage.Client()
    bucket = storage_client.bucket(PROCESSED_BUCKET)
    blob_name = f"books/{domain}/{book_id}_chunks.jsonl"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(approved_file, content_type='application/jsonl')
    gcs_uri = f"gs://{PROCESSED_BUCKET}/{blob_name}"

    # BigQuery Ingestion
    bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
    
    # 1. Clean previous rows for this book_id to guarantee zero duplicates
    del_chunks_sql = f"DELETE FROM `{PROJECT_ID}.{BQ_DATASET}.book_chunks` WHERE book_id = '{book_id}'"
    del_embed_sql = f"DELETE FROM `{PROJECT_ID}.{BQ_DATASET}.book_embeddings` WHERE book_id = '{book_id}'"
    bq_client.query(del_chunks_sql).result()
    bq_client.query(del_embed_sql).result()
    
    # 2. Append new approved chunks
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )
    table_id = f"{PROJECT_ID}.{BQ_DATASET}.book_chunks"
    load_job = bq_client.load_table_from_uri(gcs_uri, table_id, job_config=job_config)
    load_job.result()

    # 3. Generate embeddings only for this book_id
    embed_sql = f"""
    INSERT INTO `{PROJECT_ID}.{BQ_DATASET}.book_embeddings`
    SELECT
      base.chunk_id,
      base.book_id,
      base.source_type,
      base.domain,
      base.chapter,
      base.section,
      base.page_start,
      base.page_end,
      base.content,
      base.content_type,
      base.topics,
      base.review_confidence,
      base.source_uri,
      embed.ml_generate_embedding_result AS embedding,
      base.created_at
    FROM ML.GENERATE_EMBEDDING(
      MODEL `{PROJECT_ID}.{BQ_DATASET}.embedding_model`,
      (SELECT * FROM `{PROJECT_ID}.{BQ_DATASET}.book_chunks` WHERE book_id = '{book_id}'),
      STRUCT('RETRIEVAL_DOCUMENT' AS task_type)
    ) AS embed
    JOIN `{PROJECT_ID}.{BQ_DATASET}.book_chunks` AS base
      ON embed.chunk_id = base.chunk_id
    WHERE embed.ml_generate_embedding_status = '';
    """
    bq_client.query(embed_sql).result()
    
    # Verification query
    v_sql = f"""
    SELECT 
      (SELECT count(*) FROM `{PROJECT_ID}.{BQ_DATASET}.book_chunks` WHERE book_id = '{book_id}') as chunks_count,
      (SELECT count(*) FROM `{PROJECT_ID}.{BQ_DATASET}.book_embeddings` WHERE book_id = '{book_id}') as embed_count,
      (SELECT ANY_VALUE(ARRAY_LENGTH(embedding)) FROM `{PROJECT_ID}.{BQ_DATASET}.book_embeddings` WHERE book_id = '{book_id}') as dim,
      (SELECT count(*) - count(distinct chunk_id) FROM `{PROJECT_ID}.{BQ_DATASET}.book_chunks` WHERE book_id = '{book_id}') as dup_chunks,
      (SELECT count(*) - count(distinct chunk_id) FROM `{PROJECT_ID}.{BQ_DATASET}.book_embeddings` WHERE book_id = '{book_id}') as dup_embeds,
      (SELECT countif(embedding is null or ARRAY_LENGTH(embedding) = 0) FROM `{PROJECT_ID}.{BQ_DATASET}.book_embeddings` WHERE book_id = '{book_id}') as failed_embeds
    """
    v_res = list(bq_client.query(v_sql).result())[0]
    return gcs_uri, v_res

def main():
    parser = argparse.ArgumentParser(description="Process a book for Darukaa.Earth RAG")
    parser.add_argument("--pdf", required=True, help="Local PDF path")
    parser.add_argument("--book-id", required=True, help="Unique Book ID")
    parser.add_argument("--domain", required=True, help="Domain name")
    parser.add_argument("--source-uri", required=True, help="Original GCS URI")
    args = parser.parse_args()

    print(f"\n=======================================================")
    print(f"PROCESSING BOOK: {args.book_id} ({args.domain})")
    print(f"SOURCE URI:      {args.source_uri}")
    print(f"=======================================================")

    pages, raw_chunks = parse_and_chunk_pdf(args.pdf, args.book_id, args.domain, args.source_uri)
    print(f"Parsed {pages} pages -> {len(raw_chunks)} raw semantic chunks.")

    print(f"Filtering {len(raw_chunks)} chunks with Gemini 2.5 Flash...")
    reviewed = review_chunks_with_llm(raw_chunks)
    
    retained = [c for c in reviewed if c['keep']]
    discarded = [c for c in reviewed if not c['keep']]
    
    discard_cats = {}
    for d in discarded:
        cat = d.get('chapter', 'Other')
        discard_cats[cat] = discard_cats.get(cat, 0) + 1

    print(f"Review complete: KEEP={len(retained)}, DISCARD={len(discarded)}")
    print(f"Discard categories: {discard_cats}")

    gcs_uri, bq_stats = save_and_ingest(args.book_id, args.domain, reviewed)
    
    print("\n-------------------------------------------------------")
    print("BOOK VALIDATION SUMMARY")
    print("-------------------------------------------------------")
    print(f"PDF Filename:             {os.path.basename(args.pdf)}")
    print(f"Domain:                   {args.domain}")
    print(f"Page Count:               {pages}")
    print(f"Chunks Before Filtering:  {len(raw_chunks)}")
    print(f"KEEP Count:               {len(retained)}")
    print(f"DISCARD Count:            {len(discarded)}")
    print(f"Discard Categories:       {discard_cats}")
    print(f"Final book_chunks Rows:   {bq_stats['chunks_count']}")
    print(f"Final Embedding Rows:     {bq_stats['embed_count']}")
    print(f"Embedding Dimensionality: {bq_stats['dim']}")
    print(f"Duplicate Count:          {bq_stats['dup_chunks'] + bq_stats['dup_embeds']}")
    print(f"Embedding Failures:       {bq_stats['failed_embeds']}")
    print(f"Processed GCS Path:       {gcs_uri}")

    print("\n--- SAMPLE DISCARDED CHUNKS (Showing 2) ---")
    for d in discarded[:2]:
        print(f"\nChunk ID: {d['chunk_id']} (Pages {d['page_start']}-{d['page_end']})")
        print(f"Section:  {d['chapter']} - {d['section']}")
        print(f"Reason:   {d['review_reason']}")
        print(f"Content:  {repr(d['content'][:150])}...")

    print("\n--- SAMPLE RETAINED CHUNKS (Showing 2) ---")
    for r in retained[:2]:
        print(f"\nChunk ID: {r['chunk_id']} (Pages {r['page_start']}-{r['page_end']})")
        print(f"Section:  {r['chapter']} - {r['section']}")
        print(f"Reason:   {r['review_reason']}")
        print(f"Content:  {repr(r['content'][:150])}...")

if __name__ == '__main__':
    main()
