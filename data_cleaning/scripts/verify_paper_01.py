#!/usr/bin/env python3
import json
from google.cloud import bigquery, storage

with open('scratch/paper_01_validation_report.json', 'r') as f:
    report = json.load(f)

bq = bigquery.Client(project='developer-491706', location='US')

print('=== 1. BIGQUERY COUNTS & MATCH CHECKS ===')
q_match = """
SELECT 
  (SELECT count(*) FROM `developer-491706.darukaa_kb.research_papers`) as papers_count,
  (SELECT count(*) FROM `developer-491706.darukaa_kb.research_evidence`) as ev_count,
  (SELECT count(*) FROM `developer-491706.darukaa_kb.research_embeddings`) as em_count,
  (SELECT count(*) FROM `developer-491706.darukaa_kb.research_embeddings` e 
   LEFT JOIN `developer-491706.darukaa_kb.research_evidence` ev ON e.evidence_id = ev.evidence_id 
   WHERE ev.evidence_id IS NULL) as orphan_embeddings,
  (SELECT ANY_VALUE(ARRAY_LENGTH(embedding)) FROM `developer-491706.darukaa_kb.research_embeddings`) as dim,
  (SELECT count(*) - count(distinct evidence_id) FROM `developer-491706.darukaa_kb.research_evidence`) as dup_evidence,
  (SELECT count(*) - count(distinct evidence_id) FROM `developer-491706.darukaa_kb.research_embeddings`) as dup_embeddings
"""
m_res = list(bq.query(q_match).result())[0]
print('Counts & matches:', dict(m_res))

# Check references in research_evidence
q_ref = """
SELECT count(*) as ref_count 
FROM `developer-491706.darukaa_kb.research_evidence`
WHERE LOWER(section) LIKE '%reference%' 
   OR LOWER(section) LIKE '%bibliog%'
   OR content_type = 'references'
"""
ref_count = list(bq.query(q_ref).result())[0]['ref_count']
print(f'Reference units in research_evidence: {ref_count}')

# Check affiliations/acknowledgments
q_aff = """
SELECT count(*) as aff_count 
FROM `developer-491706.darukaa_kb.research_evidence`
WHERE LOWER(section) LIKE '%affiliation%' 
   OR LOWER(section) LIKE '%acknowledg%'
   OR content_type = 'boilerplate'
"""
aff_count = list(bq.query(q_aff).result())[0]['aff_count']
print(f'Affiliation/acknowledgment units in research_evidence: {aff_count}')

# Check book layer was NOT modified
q_books = """
SELECT count(*) as total_books_chunks,
  (SELECT count(*) FROM `developer-491706.darukaa_kb.book_embeddings`) as total_books_embeds
FROM `developer-491706.darukaa_kb.book_chunks`
"""
b_res = list(bq.query(q_books).result())[0]
print('Book layer integrity (must be 615):', dict(b_res))

# Check GCS bucket integrity
gcs = storage.Client()
mumbai_bucket = gcs.bucket('agriculture_rag_data')
mumbai_blobs = list(mumbai_bucket.list_blobs(prefix='papers/'))
print(f'Mumbai papers/ blobs: {len(mumbai_blobs)}')
for b in sorted(mumbai_blobs, key=lambda x: x.name):
    if b.size > 0:
        print(f'  {b.name} ({b.size} bytes, updated: {b.updated})')

print('\n=== 2. 5 SAMPLE RETAINED EVIDENCE UNITS ===')
for i, u in enumerate(report['retained_samples'][:5], start=1):
    print(f"\n--- Retained Sample {i} ({u['evidence_id']}) ---")
    print(f"Section:      {u['section']}")
    print(f"Pages:        {u['page_start']}-{u['page_end']}")
    print(f"Content Type: {u.get('content_type')}")
    print(f"Variables:    {u.get('variables')}")
    print(f"Relationship: {u.get('relationship')}")
    print(f"Direction:    {u.get('direction')}")
    print(f"Outcome:      {u.get('outcome')}")
    print(f"Magnitude:    {u.get('magnitude')}")
    print(f"Limitations:  {u.get('limitations')}")
    print(f"Text Preview: {repr(u['evidence_text'][:250])}...")

print('\n=== 3. 5 SAMPLE DISCARDED UNITS ===')
for i, u in enumerate(report['discarded_samples'][:5], start=1):
    print(f"\n--- Discarded Sample {i} ({u['evidence_id']}) ---")
    print(f"Section:      {u['section']}")
    print(f"Pages:        {u['page_start']}-{u['page_end']}")
    print(f"Content Type: {u.get('content_type')}")
    print(f"Reason:       {u.get('review_reason')}")
    print(f"Text Preview: {repr(u['evidence_text'][:200])}...")
