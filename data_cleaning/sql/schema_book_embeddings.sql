CREATE TABLE IF NOT EXISTS `developer-491706.darukaa_kb.book_embeddings` (
  chunk_id STRING NOT NULL,
  book_id STRING NOT NULL,
  source_type STRING NOT NULL,
  domain STRING NOT NULL,
  chapter STRING,
  section STRING,
  page_start INT64,
  page_end INT64,
  content STRING NOT NULL,
  content_type STRING NOT NULL,
  topics ARRAY<STRING>,
  review_confidence FLOAT64,
  source_uri STRING NOT NULL,
  embedding ARRAY<FLOAT64> NOT NULL,
  created_at TIMESTAMP NOT NULL
);
