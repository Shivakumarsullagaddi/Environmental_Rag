CREATE OR REPLACE TABLE `developer-491706.darukaa_kb.book_embeddings` AS
SELECT
  base.chunk_id,
  base.book_id,
  base.source_type,
  base.domain,
  base.chapter,
  base.section,
  base.page_start,
  base.page_end,
  base.content,
  base.content_type,
  base.topics,
  base.review_confidence,
  base.source_uri,
  embed.ml_generate_embedding_result AS embedding,
  base.created_at
FROM ML.GENERATE_EMBEDDING(
  MODEL `developer-491706.darukaa_kb.embedding_model`,
  TABLE `developer-491706.darukaa_kb.book_chunks`,
  STRUCT('RETRIEVAL_DOCUMENT' AS task_type)
) AS embed
JOIN `developer-491706.darukaa_kb.book_chunks` AS base
  ON embed.chunk_id = base.chunk_id
WHERE embed.ml_generate_embedding_status = '';
