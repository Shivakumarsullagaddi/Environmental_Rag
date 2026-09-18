#!/usr/bin/env python3
"""
Darukaa.Earth Pipeline - Phase 6 & 7: Knowledge Filtering with Gemini 2.5 Flash
Performs strict scientific relevance evaluation of every chunk.
Rejects front matter, bibliographies, indices, attributions, and non-knowledge boilerplate.
Uploads only approved KEEP chunks to US Cloud Storage.
"""

import os
import sys
import json
import time
from google import genai
from google.genai import types
from google.cloud import storage

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'pipeline_config.json')

with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRATCH_DIR = os.path.join(PROJECT_ROOT, 'scratch')

RAW_CHUNKS_FILE = os.path.join(SCRATCH_DIR, 'raw_chunks.jsonl')
REVIEWED_CHUNKS_FILE = os.path.join(SCRATCH_DIR, 'reviewed_chunks.jsonl')
APPROVED_CHUNKS_FILE = os.path.join(SCRATCH_DIR, 'approved_chunks.jsonl')

REGIONS = ['us-east4', 'us-central1', 'us-west1']
clients = {
    loc: genai.Client(vertexai=True, project=config['project_id'], location=loc)
    for loc in REGIONS
}

ALLOWED_TYPES = {
    "concept", "definition", "method", "finding",
    "discussion", "recommendation", "table", "figure", "other", "boilerplate"
}

BATCH_PROMPT_TEMPLATE = """You are the Knowledge Filter for the Darukaa.Earth Environmental RAG system.
Evaluate each chunk extracted from an environmental science textbook to decide whether it should enter the clean knowledge base.

FILTERING RULES:
1. KEEP (keep: true):
   - Fundamental scientific concepts, definitions, scientific explanations, environmental relationships.
   - Scientific methods, sampling procedures, taxonomic classification, quantitative formulas and metrics.
   - Empirical findings, ecosystem dynamics, case studies, biodiversity values (ecological, utilitarian).
   - Structured scientific data tables and informative figure captions.

2. DISCARD (keep: false):
   - Front matter: title/cover pages, publisher info, copyright notices, author biographies, ISBN.
   - Table of Contents and navigation-only text.
   - Bibliographies, literature references, and citation lists (e.g. "[1] Brown et al...", journal citations).
   - Index of terms, keywords, and page numbers.
   - Module attributions, collection licensing, Creative Commons boilerplate, and URL metadata.
   - Student classroom activity logistics (e.g., instructions to cut paper cards, student group assignments) that do not contain scientific knowledge.
   - Obvious noise, empty fragments, or promotional book summaries.

CRITICAL:
- You MUST NOT edit, summarize, or rewrite the original chunk text.
- Return a JSON list of objects, one for each chunk in the input list, matching this schema:
[
  {{
    "chunk_id": "string matching the given CHUNK_ID",
    "keep": boolean,
    "content_type": "concept" | "definition" | "method" | "finding" | "discussion" | "recommendation" | "table" | "figure" | "other" | "boilerplate",
    "reason": "Clear concise scientific explanation for decision",
    "confidence": float between 0.0 and 1.0,
    "topics": ["list", "of", "relevant", "scientific", "topics"]
  }}
]

Chunks to evaluate:
{chunks_payload}
"""

def review_batch(batch, region_idx=0, max_retries=4):
    chunks_text = []
    for c in batch:
        chunks_text.append(f"---\nCHUNK_ID: {c['chunk_id']}\nCHAPTER: {c.get('chapter', '')}\nSECTION: {c.get('section', '')}\nCONTENT:\n{c.get('content', '')}")
    
    payload = "\n".join(chunks_text)
    prompt = BATCH_PROMPT_TEMPLATE.format(chunks_payload=payload)

    for attempt in range(max_retries):
        loc = REGIONS[(region_idx + attempt) % len(REGIONS)]
        client = clients[loc]
        try:
            resp = client.models.generate_content(
                model=config['gemini_model'],
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
            
            merged = []
            for c in batch:
                if c['chunk_id'] in results_map:
                    merged.append({**c, **results_map[c['chunk_id']]})
                else:
                    merged.append({
                        **c,
                        'keep': False,
                        'content_type': 'boilerplate',
                        'review_reason': 'Unmatched chunk in LLM response',
                        'review_confidence': 0.5,
                        'topics': []
                    })
            return merged

        except Exception as e:
            print(f"Batch review attempt {attempt+1} on {loc} failed: {e}")
            time.sleep(2.0 * (attempt + 1))

    print(f"Batch review failed after {max_retries} attempts.")
    return [{
        **c,
        'keep': False,
        'content_type': 'boilerplate',
        'review_reason': 'LLM review request failed',
        'review_confidence': 0.0,
        'topics': []
    } for c in batch]

def main():
    if not os.path.exists(RAW_CHUNKS_FILE):
        print(f"Error: {RAW_CHUNKS_FILE} not found.")
        sys.exit(1)

    # Clean old state
    for old_file in [REVIEWED_CHUNKS_FILE, APPROVED_CHUNKS_FILE]:
        if os.path.exists(old_file):
            os.remove(old_file)

    with open(RAW_CHUNKS_FILE, 'r', encoding='utf-8') as f:
        chunks = [json.loads(line) for line in f]

    batch_size = 4
    batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    print(f"Starting Gemini LLM review for {len(chunks)} chunks in {len(batches)} batches using {config['gemini_model']}...")
    start_time = time.time()

    reviewed_chunks = []
    for idx, b in enumerate(batches, 1):
        print(f"Reviewing batch {idx}/{len(batches)} ({len(b)} chunks)...")
        b_results = review_batch(b, region_idx=idx)
        reviewed_chunks.extend(b_results)
        for r in b_results:
            status_str = "KEEP" if r['keep'] else "DISCARD"
            print(f"  {r['chunk_id']} [{r.get('chapter', '')[:20]}] -> {status_str} ({r['content_type']}, conf={r['review_confidence']:.2f})")
        time.sleep(1.0)

    elapsed = time.time() - start_time
    print(f"\nCompleted review of {len(reviewed_chunks)} chunks in {elapsed:.1f}s")

    reviewed_chunks.sort(key=lambda x: x['chunk_id'])

    approved_chunks = [c for c in reviewed_chunks if c['keep']]
    discarded_chunks = [c for c in reviewed_chunks if not c['keep']]

    print(f"\n=======================================================")
    print(f"LLM FILTER SUMMARY:")
    print(f"Total Chunks:   {len(reviewed_chunks)}")
    print(f"KEEP Count:     {len(approved_chunks)}")
    print(f"DISCARD Count:  {len(discarded_chunks)}")
    
    # Categorize discarded
    discard_cats = {}
    for c in discarded_chunks:
        ctype = c['content_type']
        discard_cats[ctype] = discard_cats.get(ctype, 0) + 1
    print(f"Discarded Categories: {discard_cats}")
    print(f"=======================================================\n")

    # Save reviewed chunks
    with open(REVIEWED_CHUNKS_FILE, 'w', encoding='utf-8') as f:
        for c in reviewed_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + '\n')

    # Save approved chunks
    final_approved_records = []
    for c in approved_chunks:
        final_approved_records.append({
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
        })

    with open(APPROVED_CHUNKS_FILE, 'w', encoding='utf-8') as f:
        for c in final_approved_records:
            f.write(json.dumps(c, ensure_ascii=False) + '\n')

    print(f"Saved {len(final_approved_records)} approved chunks to {APPROVED_CHUNKS_FILE}")

    # Overwrite in US Cloud Storage
    storage_client = storage.Client()
    dest_bucket = storage_client.bucket(config['processed_bucket'])
    target_blob_name = f"books/{config['domain']}/{config['target_book_id']}_chunks.jsonl"
    blob = dest_bucket.blob(target_blob_name)
    blob.upload_from_filename(APPROVED_CHUNKS_FILE, content_type='application/jsonl')
    dest_gcs_uri = f"gs://{config['processed_bucket']}/{target_blob_name}"
    print(f"Cleanly uploaded approved chunks to US Storage: {dest_gcs_uri}")

if __name__ == '__main__':
    main()
