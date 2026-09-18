#!/usr/bin/env python3
"""
Darukaa.Earth Pipeline - Phase 8 & 9: BigQuery Ingestion and Embedding Generation
Loads approved chunks into darukaa_kb.book_chunks, creates the remote embedding model,
and generates vector embeddings via BigQuery ML.
Also initializes schema for research_evidence and environmental datasets.
"""

import os
import sys
import json
import time
from google.cloud import bigquery

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'pipeline_config.json')

with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

PROJECT_ID = config['project_id']
DATASET_ID = config['bigquery_dataset']
LOCATION = config['bigquery_location']
CONNECTION_ID = config['bq_connection']
EMBEDDING_MODEL = config['embedding_model']

GCS_APPROVED_URI = f"gs://{config['processed_bucket']}/books/{config['domain']}/{config['target_book_id']}_chunks.jsonl"

client = bigquery.Client(project=PROJECT_ID, location=LOCATION)

def setup_dataset():
    dataset_ref = bigquery.DatasetReference(PROJECT_ID, DATASET_ID)
    try:
        client.get_dataset(dataset_ref)
        print(f"Dataset {DATASET_ID} already exists in {LOCATION}.")
    except Exception:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = LOCATION
        client.create_dataset(dataset, timeout=30)
        print(f"Created dataset {DATASET_ID} in {LOCATION}.")

def load_book_chunks():
    table_id = f"{PROJECT_ID}.{DATASET_ID}.book_chunks"
    
    schema = [
        bigquery.SchemaField("chunk_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("book_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source_type", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("domain", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("chapter", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("section", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("page_start", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("page_end", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("content", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("content_type", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("topics", "STRING", mode="REPEATED"),
        bigquery.SchemaField("review_confidence", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("source_uri", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
    ]

    job_config = bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    print(f"Loading approved chunks from {GCS_APPROVED_URI} into {table_id}...")
    load_job = client.load_table_from_uri(
        GCS_APPROVED_URI,
        table_id,
        job_config=job_config
    )
    load_job.result() # Wait for job to complete
    table = client.get_table(table_id)
    print(f"Successfully loaded {table.num_rows} rows into {table_id}.")
    return table.num_rows

def setup_remote_model():
    model_id = f"`{PROJECT_ID}.{DATASET_ID}.embedding_model`"
    conn_id = f"`{CONNECTION_ID}`"
    sql = f"""
    CREATE OR REPLACE MODEL {model_id}
    REMOTE WITH CONNECTION {conn_id}
    OPTIONS (ENDPOINT = '{EMBEDDING_MODEL}');
    """
    print(f"Creating BigQuery remote model {model_id} pointing to {EMBEDDING_MODEL}...")
    query_job = client.query(sql)
    query_job.result()
    print(f"Successfully created remote model {model_id}.")

def generate_embeddings():
    table_id = f"`{PROJECT_ID}.{DATASET_ID}.book_embeddings`"
    chunks_table = f"`{PROJECT_ID}.{DATASET_ID}.book_chunks`"
    model_id = f"`{PROJECT_ID}.{DATASET_ID}.embedding_model`"

    print(f"Generating vector embeddings using {model_id} with task_type='RETRIEVAL_DOCUMENT'...")
    sql = f"""
    CREATE OR REPLACE TABLE {table_id} AS
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
      MODEL {model_id},
      TABLE {chunks_table},
      STRUCT('RETRIEVAL_DOCUMENT' AS task_type)
    ) AS embed
    JOIN {chunks_table} AS base
      ON embed.chunk_id = base.chunk_id
    WHERE embed.ml_generate_embedding_status = '';
    """
    query_job = client.query(sql)
    query_job.result()
    
    res_table = client.get_table(f"{PROJECT_ID}.{DATASET_ID}.book_embeddings")
    print(f"Successfully generated and stored {res_table.num_rows} embeddings in {table_id}.")
    return res_table.num_rows

def setup_auxiliary_schemas():
    print("Setting up schemas for Phase 10 (Research Evidence) and Phase 11 (Environmental Tables)...")
    
    # Research evidence
    re_sql = f"""
    CREATE TABLE IF NOT EXISTS `{PROJECT_ID}.{DATASET_ID}.research_evidence` (
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
    """
    client.query(re_sql).result()

    # Environmental observation tables
    env_sql = f"""
    CREATE TABLE IF NOT EXISTS `{PROJECT_ID}.{DATASET_ID}.soil_data` (
      record_id STRING NOT NULL,
      latitude FLOAT64 NOT NULL,
      longitude FLOAT64 NOT NULL,
      date DATE NOT NULL,
      soil_ph FLOAT64,
      organic_carbon FLOAT64,
      soil_moisture FLOAT64,
      source_dataset STRING
    );

    CREATE TABLE IF NOT EXISTS `{PROJECT_ID}.{DATASET_ID}.climate_data` (
      record_id STRING NOT NULL,
      latitude FLOAT64 NOT NULL,
      longitude FLOAT64 NOT NULL,
      date DATE NOT NULL,
      temperature FLOAT64,
      rainfall FLOAT64,
      humidity FLOAT64,
      source_dataset STRING
    );

    CREATE TABLE IF NOT EXISTS `{PROJECT_ID}.{DATASET_ID}.lulc_data` (
      record_id STRING NOT NULL,
      latitude FLOAT64 NOT NULL,
      longitude FLOAT64 NOT NULL,
      year INT64 NOT NULL,
      land_cover STRING,
      confidence FLOAT64,
      source_dataset STRING
    );

    CREATE TABLE IF NOT EXISTS `{PROJECT_ID}.{DATASET_ID}.biodiversity_data` (
      record_id STRING NOT NULL,
      latitude FLOAT64 NOT NULL,
      longitude FLOAT64 NOT NULL,
      species STRING NOT NULL,
      observation_date DATE,
      kingdom STRING,
      family STRING,
      count INT64,
      source_dataset STRING
    );
    """
    client.query(env_sql).result()
    print("Auxiliary tables initialized successfully.")

def main():
    setup_dataset()
    chunk_rows = load_book_chunks()
    setup_remote_model()
    embed_rows = generate_embeddings()
    setup_auxiliary_schemas()
    print(f"\nBigQuery ingestion and embedding generation complete.")
    print(f"Chunks loaded: {chunk_rows}")
    print(f"Embeddings generated: {embed_rows}")

if __name__ == '__main__':
    main()
