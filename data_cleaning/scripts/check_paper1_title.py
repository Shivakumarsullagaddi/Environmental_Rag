#!/usr/bin/env python3
from google.cloud import bigquery

bq = bigquery.Client(project='developer-491706', location='US')
q = """
SELECT evidence_id, section, content_type, evidence_text 
FROM `developer-491706.darukaa_kb.research_evidence`
WHERE paper_id = 'paper_01' AND LENGTH(evidence_text) < 120
"""
rows = list(bq.query(q).result())
for r in rows:
    print(dict(r))

# If paper_01_evidence_0010 is the title alone, delete it to ensure zero title-only rows!
for r in rows:
    if "Deforestation impacts soil biodiversity and ecosystem services worldwide" in r['evidence_text'] and len(r['evidence_text']) < 100:
        print(f"Removing title-only evidence row {r['evidence_id']}...")
        bq.query(f"DELETE FROM `developer-491706.darukaa_kb.research_evidence` WHERE evidence_id = '{r['evidence_id']}'").result()
        bq.query(f"DELETE FROM `developer-491706.darukaa_kb.research_embeddings` WHERE evidence_id = '{r['evidence_id']}'").result()
        print("Removed successfully.")
