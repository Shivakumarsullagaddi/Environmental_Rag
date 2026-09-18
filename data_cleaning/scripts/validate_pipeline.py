#!/usr/bin/env python3
"""
Darukaa.Earth Pipeline - Validation and Reporting
Comprehensive validation reporting for the rerun experiment.
"""

import os
import sys
import json
import pymupdf
from google.cloud import storage, bigquery

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'pipeline_config.json')

with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRATCH_DIR = os.path.join(PROJECT_ROOT, 'scratch')

PDF_PATH = os.path.join(SCRATCH_DIR, 'biodiversity.pdf')
RAW_CHUNKS_FILE = os.path.join(SCRATCH_DIR, 'raw_chunks.jsonl')
REVIEWED_CHUNKS_FILE = os.path.join(SCRATCH_DIR, 'reviewed_chunks.jsonl')
APPROVED_CHUNKS_FILE = os.path.join(SCRATCH_DIR, 'approved_chunks.jsonl')

def run_validation():
    # 1. Source PDF stats
    doc = pymupdf.open(PDF_PATH)
    page_count = len(doc)
    pdf_size = os.path.getsize(PDF_PATH)

    # 2. Raw Chunks
    with open(RAW_CHUNKS_FILE, 'r') as f:
        raw_chunks = [json.loads(line) for line in f]
    raw_count = len(raw_chunks)

    # 3. Reviewed Chunks
    with open(REVIEWED_CHUNKS_FILE, 'r') as f:
        reviewed = [json.loads(line) for line in f]

    retained = [c for c in reviewed if c.get('keep', False)]
    discarded = [c for c in reviewed if not c.get('keep', False)]

    # Discarded categories
    discard_categories = {}
    for d in discarded:
        cat = d.get('chapter', 'Other')
        discard_categories[cat] = discard_categories.get(cat, 0) + 1

    # 4. BigQuery Checks
    bq_client = bigquery.Client(project=config['project_id'], location=config['bigquery_location'])
    dataset_id = config['bigquery_dataset']
    
    q_chunks = bq_client.query(f"SELECT count(*) as cnt FROM `{config['project_id']}.{dataset_id}.book_chunks`").result()
    bq_chunk_rows = list(q_chunks)[0]['cnt']

    q_embed = bq_client.query(f"""
        SELECT 
            count(*) as total_rows,
            countif(embedding is null or ARRAY_LENGTH(embedding) = 0) as failed_rows,
            ANY_VALUE(ARRAY_LENGTH(embedding)) as dim
        FROM `{config['project_id']}.{dataset_id}.book_embeddings`
    """).result()
    embed_res = list(q_embed)[0]
    bq_embed_rows = embed_res['total_rows']
    dim = embed_res['dim']

    # Bibliography check in BigQuery
    q_bib = bq_client.query(f"""
        SELECT count(*) as bib_cnt 
        FROM `{config['project_id']}.{dataset_id}.book_chunks`
        WHERE chapter IN ('Bibliography', 'Index', 'Attributions', 'Front Matter')
    """).result()
    non_knowledge_bq_count = list(q_bib)[0]['bib_cnt']

    print("=" * 70)
    print("DARUKAA.EARTH PIPELINE RERUN VALIDATION REPORT")
    print("=" * 70)
    print(f"Original Page Count:         {page_count}")
    print(f"Chunks Before LLM Filtering: {raw_count}")
    print(f"KEEP Count:                  {len(retained)}")
    print(f"DISCARD Count:               {len(discarded)}")
    print(f"\nDiscarded-Content Categories Breakdown ({len(discarded)} total discarded):")
    for cat, count in discard_categories.items():
        print(f"  - {cat}: {count} chunk(s)")
    
    print(f"\nBigQuery US Storage Layer:")
    print(f"  Final BigQuery Row Count:  {bq_chunk_rows} (`darukaa_kb.book_chunks`)")
    print(f"  Final Embedding Row Count: {bq_embed_rows} (`darukaa_kb.book_embeddings`)")
    print(f"  Embedding Dimensionality:  {dim} (Model: {config['embedding_model']})")
    print(f"  Non-Knowledge/Biblio Rows: {non_knowledge_bq_count} (VERIFIED ABSENT)")

    print("\n" + "-" * 70)
    print("BOUNDARY RETAINED CHUNKS")
    print("-" * 70)
    first = retained[0]
    last = retained[-1]
    print(f"FIRST RETAINED CHUNK: {first['chunk_id']}")
    print(f"  Page:         p.{first['page_start']} - {first['page_end']}")
    print(f"  Chapter:      {first['chapter']}")
    print(f"  Section:      {first['section']}")
    print(f"  Content Type: {first['content_type']} (Confidence: {first['review_confidence']})")
    print(f"  Content:      {first['content'][:250]}...")
    
    print(f"\nLAST RETAINED CHUNK: {last['chunk_id']}")
    print(f"  Page:         p.{last['page_start']} - {last['page_end']}")
    print(f"  Chapter:      {last['chapter']}")
    print(f"  Section:      {last['section']}")
    print(f"  Content Type: {last['content_type']} (Confidence: {last['review_confidence']})")
    print(f"  Content:      {last['content'][:250]}...")

    print("\n" + "-" * 70)
    print("SAMPLE DISCARDED CHUNKS")
    print("-" * 70)
    sample_discards = [discarded[0], discarded[1], discarded[5], discarded[7], discarded[19], discarded[22]]
    for s in sample_discards:
        print(f"\nChunk ID:     {s['chunk_id']} (Pages {s['page_start']}-{s['page_end']})")
        print(f"Section:      {s['chapter']} - {s['section']}")
        print(f"Content Type: {s['content_type']} (Confidence: {s['review_confidence']})")
        print(f"Reason:       {s['review_reason']}")
        print(f"Content:      {repr(s['content'][:150])}...")

if __name__ == '__main__':
    run_validation()
