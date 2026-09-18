-- Schema for Phase 10: Research Papers Evidence
-- Scientific body -> chunk + embed
-- References -> preserved as citation metadata
CREATE TABLE IF NOT EXISTS `developer-491706.darukaa_kb.research_evidence` (
  evidence_id STRING NOT NULL,
  paper_id STRING NOT NULL,
  title STRING NOT NULL,
  authors ARRAY<STRING>,
  year INT64,
  domain STRING NOT NULL,
  variables ARRAY<STRING>,
  relationship STRING,
  study_location STRING,
  study_period STRING,
  evidence_text STRING NOT NULL,
  intervention STRING,
  outcome STRING,
  limitations STRING,
  doi STRING,
  source_uri STRING NOT NULL,
  embedding ARRAY<FLOAT64>,
  created_at TIMESTAMP NOT NULL
);
