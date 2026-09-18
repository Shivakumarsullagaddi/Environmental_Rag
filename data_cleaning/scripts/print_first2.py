#!/usr/bin/env python3
from google.cloud import bigquery

bq = bigquery.Client(project='developer-491706', location='US')
q = """
SELECT evidence_id, section, page_start, page_end, content_type, evidence_text, 
       variables, relationship, direction, outcome, magnitude, limitations
FROM `developer-491706.darukaa_kb.research_evidence`
ORDER BY evidence_id
LIMIT 2
"""
for idx, r in enumerate(bq.query(q).result(), start=1):
    print(f"=== UNIT {idx}: {r['evidence_id']} (Section: {r['section']}, Pages: {r['page_start']}-{r['page_end']}) ===")
    print(f"Content Type: {r['content_type']}")
    print(f"Variables:    {r['variables']}")
    print(f"Relationship: {r['relationship']}")
    print(f"Direction:    {r['direction']}")
    print(f"Outcome:      {r['outcome']}")
    print(f"Magnitude:    {r['magnitude']}")
    print(f"Limitations:  {r['limitations']}")
    print(f"Evidence Text:\n{r['evidence_text']}\n")
