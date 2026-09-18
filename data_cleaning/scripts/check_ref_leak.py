#!/usr/bin/env python3
from google.cloud import bigquery

bq = bigquery.Client(project='developer-491706', location='US')
q = """
SELECT evidence_id, paper_id, section, content_type, SUBSTR(evidence_text, 1, 150) as preview
FROM `developer-491706.darukaa_kb.research_evidence`
WHERE LOWER(section) LIKE '%reference%' 
   OR LOWER(section) LIKE '%bibliog%' 
   OR content_type = 'references'
"""
for r in bq.query(q).result():
    print(dict(r))
