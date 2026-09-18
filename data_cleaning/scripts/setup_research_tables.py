#!/usr/bin/env python3
from google.cloud import bigquery

bq = bigquery.Client(project='developer-491706', location='US')

q1 = """
CREATE TABLE IF NOT EXISTS `developer-491706.darukaa_kb.research_papers` (
  paper_id STRING NOT NULL,
  title STRING,
  abstract STRING,
  authors ARRAY<STRING>,
  year INT64,
  doi STRING,
  journal STRING,
  study_type STRING,
  domain STRING,
  source_uri STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
);
"""

q2 = """
CREATE OR REPLACE TABLE `developer-491706.darukaa_kb.research_evidence` (
  evidence_id STRING NOT NULL,
  paper_id STRING NOT NULL,
  section STRING,
  page_start INT64,
  page_end INT64,
  evidence_text STRING NOT NULL,
  content_type STRING,
  variables ARRAY<STRING>,
  relationship STRING,
  direction STRING,
  outcome STRING,
  magnitude STRING,
  study_location STRING,
  study_period STRING,
  sample_size STRING,
  study_design STRING,
  data_source STRING,
  measurement_method STRING,
  statistical_method STRING,
  limitations ARRAY<STRING>,
  topics ARRAY<STRING>,
  source_uri STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
);
"""

q3 = """
CREATE TABLE IF NOT EXISTS `developer-491706.darukaa_kb.research_embeddings` (
  evidence_id STRING NOT NULL,
  embedding ARRAY<FLOAT64>
);
"""

bq.query(q1).result()
bq.query(q2).result()
bq.query(q3).result()
print('Tables setup complete.')
