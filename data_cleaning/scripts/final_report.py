#!/usr/bin/env python3
import json
from google.cloud import bigquery, storage

PROJECT_ID = 'developer-491706'
DATASET = 'darukaa_kb'
bq = bigquery.Client(project=PROJECT_ID, location='US')

print('=== 1. SUMMARY PER BOOK IN BIGQUERY ===')
query1 = f"""
SELECT 
    c.book_id,
    c.domain,
    COUNT(c.chunk_id) as chunks_count,
    COUNT(e.chunk_id) as embed_count,
    COUNT(c.chunk_id) - COUNT(DISTINCT c.chunk_id) as dup_chunks,
    COUNT(e.chunk_id) - COUNT(DISTINCT e.chunk_id) as dup_embeds,
    ANY_VALUE(ARRAY_LENGTH(e.embedding)) as embedding_dim,
    COUNTIF(e.embedding IS NULL OR ARRAY_LENGTH(e.embedding) = 0) as failed_embeds
FROM `{PROJECT_ID}.{DATASET}.book_chunks` c
LEFT JOIN `{PROJECT_ID}.{DATASET}.book_embeddings` e
  ON c.chunk_id = e.chunk_id
GROUP BY c.book_id, c.domain
ORDER BY c.domain, c.book_id;
"""
for r in bq.query(query1).result():
    print(dict(r))

print('\n=== 2. TOTAL CUMULATIVE COUNTS ===')
query2 = f"""
SELECT 
    COUNT(*) as total_chunks,
    (SELECT COUNT(*) FROM `{PROJECT_ID}.{DATASET}.book_embeddings`) as total_embeddings,
    (SELECT COUNT(*) - COUNT(DISTINCT chunk_id) FROM `{PROJECT_ID}.{DATASET}.book_chunks`) as total_dup_chunks,
    (SELECT COUNT(*) - COUNT(DISTINCT chunk_id) FROM `{PROJECT_ID}.{DATASET}.book_embeddings`) as total_dup_embeds,
    (SELECT COUNTIF(ARRAY_LENGTH(embedding) != 768) FROM `{PROJECT_ID}.{DATASET}.book_embeddings`) as non_768_dim
FROM `{PROJECT_ID}.{DATASET}.book_chunks`;
"""
for r in bq.query(query2).result():
    print(dict(r))

print('\n=== 3. CHECK BIBLIOGRAPHY / REFERENCE PURGING IN BIGQUERY ===')
query3 = f"""
SELECT 
    book_id,
    chunk_id,
    chapter,
    section,
    SUBSTR(content, 1, 100) as preview
FROM `{PROJECT_ID}.{DATASET}.book_chunks`
WHERE LOWER(chapter) LIKE '%bibliog%' 
   OR LOWER(chapter) LIKE '%referenc%'
   OR LOWER(section) LIKE '%bibliog%' 
   OR LOWER(section) LIKE '%referenc%';
"""
bib_results = list(bq.query(query3).result())
print(f'Matches for bibliography/references in BigQuery: {len(bib_results)}')
for r in bib_results:
    print(dict(r))

print('\n=== 4. VERIFY US GCS PROCESSED BUCKET ===')
gcs = storage.Client()
bucket = gcs.bucket('agriculture_rag_processed_us')
blobs = list(bucket.list_blobs(prefix='books/'))
print(f'Total processed book chunk files in US GCS: {len(blobs)}')
for b in sorted(blobs, key=lambda x: x.name):
    print(f'  {b.name} ({b.size} bytes)')

print('\n=== 5. VERIFY MUMBAI BUCKET INTEGRITY (READ-ONLY) ===')
mumbai_bucket = gcs.bucket('agriculture_rag_data')
mumbai_blobs = list(mumbai_bucket.list_blobs(prefix='books/'))
print(f'Total objects in Mumbai books/: {len(mumbai_blobs)}')
for b in sorted(mumbai_blobs, key=lambda x: x.name):
    if b.size > 0:
        print(f'  {b.name} ({b.size} bytes, updated: {b.updated})')
