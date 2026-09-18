CREATE OR REPLACE MODEL `developer-491706.darukaa_kb.embedding_model`
REMOTE WITH CONNECTION `developer-491706.us.vertex_ai_connection`
OPTIONS (ENDPOINT = 'text-embedding-004');
