#!/usr/bin/env python3
import json
from google.cloud import bigquery, storage

with open('scratch/paper_01_rebuilt_report.json', 'r') as f:
    report = json.load(f)

bq = bigquery.Client(project='developer-491706', location='US')

print('=== 1. PAPER METADATA ===')
print(json.dumps(report['metadata'], indent=2))

print('\n=== 2. BIGQUERY COUNTS & MATCH CHECKS ===')
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

# Check for forbidden strings across all research_evidence rows
FORBIDDEN = [
    "Copyright", "Creative Commons", "To whom correspondence", "Email:",
    "Published March", "Authors declare", "Direct Submission",
    "Author contributions:", "Author affiliations:"
]
print('\n=== FORBIDDEN STRING LEAKAGE CHECK ===')
q_leak = """
SELECT evidence_id, evidence_text
FROM `developer-491706.darukaa_kb.research_evidence`
"""
leaks_found = 0
for row in bq.query(q_leak).result():
    txt = row['evidence_text']
    for f_str in FORBIDDEN:
        if f_str.lower() in txt.lower():
            print(f"LEAK DETECTED in {row['evidence_id']}: found '{f_str}'")
            leaks_found += 1
if leaks_found == 0:
    print("PERFECT: Zero forbidden administrative strings found in all 34 evidence rows!")

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

print('\n=== 3. FIRST 10 RETAINED EVIDENCE UNITS (DETAILED) ===')
q_top10 = """
SELECT evidence_id, section, page_start, page_end, content_type, evidence_text, 
       variables, relationship, direction, outcome, magnitude, limitations
FROM `developer-491706.darukaa_kb.research_evidence`
ORDER BY evidence_id
LIMIT 10
"""
for idx, row in enumerate(bq.query(q_top10).result(), start=1):
    print(f"\n=======================================================")
    print(f"RETAINED UNIT {idx}: {row['evidence_id']} (Pages {row['page_start']}-{row['page_end']})")
    print(f"Section:      {row['section']}")
    print(f"Content Type: {row['content_type']}")
    print(f"Variables:    {row['variables']}")
    print(f"Relationship: {row['relationship']}")
    print(f"Direction:    {row['direction']}")
    print(f"Outcome:      {row['outcome']}")
    print(f"Magnitude:    {row['magnitude']}")
    print(f"Limitations:  {row['limitations']}")
    print(f"EVIDENCE TEXT:\n{row['evidence_text']}")

print('\n=== 4. 5 SAMPLE DISCARDED UNITS ===')
for idx, d in enumerate(report['discarded_samples'][:5], start=1):
    print(f"\n--- Discarded Sample {idx}: {d['evidence_id']} ---")
    print(f"Section:      {d['section']} (Page {d['page_start']})")
    print(f"Content Type: {d.get('content_type')}")
    print(f"Reason:       {d.get('review_reason')}")
    print(f"Candidate Text Preview: {repr(d.get('candidate_text', '')[:150])}")
