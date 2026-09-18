#!/usr/bin/env python3
import json
from google.cloud import bigquery, storage

PROJECT_ID = 'developer-491706'
DATASET = 'darukaa_kb'
bq = bigquery.Client(project=PROJECT_ID, location='US')

print('=== 1. PER-PAPER BIGQUERY SUMMARY ===')
q_papers = f"""
SELECT 
    p.paper_id,
    p.title,
    p.journal,
    p.year,
    COUNT(DISTINCT e.evidence_id) as evidence_count,
    COUNT(DISTINCT em.evidence_id) as embed_count,
    COUNT(e.evidence_id) - COUNT(DISTINCT e.evidence_id) as dup_evidence,
    COUNT(em.evidence_id) - COUNT(DISTINCT em.evidence_id) as dup_embeds,
    ANY_VALUE(ARRAY_LENGTH(em.embedding)) as embedding_dim,
    COUNTIF(em.embedding IS NULL OR ARRAY_LENGTH(em.embedding) = 0) as failed_embeds
FROM `{PROJECT_ID}.{DATASET}.research_papers` p
LEFT JOIN `{PROJECT_ID}.{DATASET}.research_evidence` e
  ON p.paper_id = e.paper_id
LEFT JOIN `{PROJECT_ID}.{DATASET}.research_embeddings` em
  ON e.evidence_id = em.evidence_id
GROUP BY p.paper_id, p.title, p.journal, p.year
ORDER BY p.paper_id;
"""
for r in bq.query(q_papers).result():
    print(dict(r))

print('\n=== 2. CUMULATIVE RESEARCH LAYER COUNTS ===')
q_totals = f"""
SELECT 
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.research_papers`) as total_papers,
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.research_evidence`) as total_evidence,
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.research_embeddings`) as total_embeddings,
    (SELECT COUNT(*) - COUNT(DISTINCT evidence_id) FROM `{PROJECT_ID}.{DATASET}.research_evidence`) as total_dup_evidence,
    (SELECT COUNT(*) - COUNT(DISTINCT evidence_id) FROM `{PROJECT_ID}.{DATASET}.research_embeddings`) as total_dup_embeds,
    (SELECT COUNTIF(ARRAY_LENGTH(embedding) != 768) FROM `{PROJECT_ID}.{DATASET}.research_embeddings`) as non_768_dim,
    (SELECT COUNTIF(embedding IS NULL OR ARRAY_LENGTH(embedding) = 0) FROM `{PROJECT_ID}.{DATASET}.research_embeddings`) as total_failed_embeds,
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.research_embeddings` em
     LEFT JOIN `{PROJECT_ID}.{DATASET}.research_evidence` ev ON em.evidence_id = ev.evidence_id
     WHERE ev.evidence_id IS NULL) as orphan_embeddings
"""
totals = list(bq.query(q_totals).result())[0]
print(dict(totals))

print('\n=== 3. VERIFY EXCLUSION OF PAPER 4 ===')
q_p4 = f"""
SELECT 
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.research_papers` WHERE paper_id LIKE '%4%') as p4_papers,
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.research_evidence` WHERE paper_id LIKE '%4%' OR evidence_id LIKE '%paper_04%') as p4_evidence,
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.research_embeddings` WHERE evidence_id LIKE '%paper_04%') as p4_embeds
"""
print('Paper 4 counts (must be 0):', dict(list(bq.query(q_p4).result())[0]))

print('\n=== 4. CHECK REFERENCES & BOILERPLATE IN RESEARCH_EVIDENCE ===')
q_noise = f"""
SELECT 
    COUNTIF(LOWER(section) LIKE '%reference%' OR LOWER(section) LIKE '%bibliog%' OR content_type = 'references') as ref_count,
    COUNTIF(LOWER(section) LIKE '%affiliation%' OR LOWER(section) LIKE '%acknowledg%' OR content_type = 'boilerplate') as bp_count,
    COUNTIF(LOWER(evidence_text) LIKE '%copyright ©%' OR LOWER(evidence_text) LIKE '%creative commons attribution%') as copyright_count,
    COUNTIF(LOWER(evidence_text) LIKE '%to whom correspondence may be addressed%') as email_count
FROM `{PROJECT_ID}.{DATASET}.research_evidence`;
"""
print('Document noise in research_evidence:', dict(list(bq.query(q_noise).result())[0]))

print('\n=== 5. VERIFY BOOK LAYER UNTOUCHED ===')
q_books = f"""
SELECT 
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.book_chunks`) as total_books_chunks,
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.book_embeddings`) as total_books_embeds
"""
print('Book layer integrity (must be 615 chunks, 615 embeds):', dict(list(bq.query(q_books).result())[0]))

print('\n=== 6. VERIFY MUMBAI SOURCE BUCKET INTEGRITY ===')
gcs = storage.Client()
bucket = gcs.bucket('agriculture_rag_data')
blobs = list(bucket.list_blobs(prefix='papers/'))
print(f'Total objects in Mumbai papers/: {len(blobs)}')
for b in sorted(blobs, key=lambda x: x.name):
    if b.size > 0:
        print(f'  {b.name} ({b.size} bytes, updated: {b.updated})')
