#!/usr/bin/env bash
# ==============================================================================
# Darukaa.Earth Environmental RAG Pipeline
# End-to-end extraction, cleaning, chunking, LLM review, US ingestion & embeddings
# ==============================================================================

set -eo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${BASE_DIR}/config/pipeline_config.json"
SCRIPTS_DIR="${BASE_DIR}/scripts"
SCRATCH_DIR="${BASE_DIR}/scratch"

mkdir -p "${SCRATCH_DIR}"

echo "=============================================================================="
echo " DARUKAA.EARTH ENVIRONMENTAL RAG PIPELINE"
echo "=============================================================================="

# Read configuration
PROJECT_ID=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['project_id'])")
SOURCE_BUCKET=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['source_bucket'])")
SOURCE_FILE=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['target_file'])")
PROCESSED_BUCKET=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['processed_bucket'])")
PROCESSED_LOC=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['processed_location'])")
BQ_DATASET=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['bigquery_dataset'])")
BQ_LOC=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['bigquery_location'])")

echo "[STEP 1] Validating GCP Project..."
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null)
if [ "${CURRENT_PROJECT}" != "${PROJECT_ID}" ]; then
    echo "Configuring GCP Project to ${PROJECT_ID}..."
    gcloud config set project "${PROJECT_ID}"
fi
echo "GCP Project confirmed: ${PROJECT_ID}"

echo -e "\n[STEP 2] Validating Mumbai Source Bucket (DO NOT MODIFY)..."
MUMBAI_LOC=$(gcloud storage buckets describe "gs://${SOURCE_BUCKET}" --format="value(location)" 2>/dev/null || echo "NOT_FOUND")
if [ "${MUMBAI_LOC}" != "ASIA-SOUTH1" ]; then
    echo "ERROR: Source bucket gs://${SOURCE_BUCKET} not found or not in ASIA-SOUTH1!"
    exit 1
fi
echo "Confirmed: gs://${SOURCE_BUCKET} exists in ${MUMBAI_LOC} (READ-ONLY SOURCE)."

echo -e "\n[STEP 3] Validating Source PDF..."
PDF_GCS_PATH="gs://${SOURCE_BUCKET}/${SOURCE_FILE}"
if ! gcloud storage ls "${PDF_GCS_PATH}" &>/dev/null; then
    echo "ERROR: Source file ${PDF_GCS_PATH} not found!"
    exit 1
fi
LOCAL_PDF="${SCRATCH_DIR}/biodiversity.pdf"
if [ ! -f "${LOCAL_PDF}" ]; then
    echo "Downloading ${PDF_GCS_PATH} to local scratch for reading..."
    gcloud storage cp "${PDF_GCS_PATH}" "${LOCAL_PDF}"
else
    echo "Source PDF verified in scratch: ${LOCAL_PDF}"
fi

echo -e "\n[STEP 4] Validating/Creating US Processed Storage Bucket..."
if ! gcloud storage buckets describe "gs://${PROCESSED_BUCKET}" &>/dev/null; then
    echo "Creating US storage bucket gs://${PROCESSED_BUCKET} in ${PROCESSED_LOC}..."
    gcloud storage buckets create "gs://${PROCESSED_BUCKET}" --location="${PROCESSED_LOC}" --uniform-bucket-level-access
else
    echo "Confirmed: gs://${PROCESSED_BUCKET} is active in US."
fi

echo -e "\n[STEP 5] Validating/Creating BigQuery US Dataset..."
if ! bq show "${PROJECT_ID}:${BQ_DATASET}" &>/dev/null; then
    echo "Creating BigQuery dataset ${PROJECT_ID}:${BQ_DATASET} in ${BQ_LOC}..."
    bq mk --location="${BQ_LOC}" --dataset "${PROJECT_ID}:${BQ_DATASET}"
else
    echo "Confirmed: BigQuery dataset ${PROJECT_ID}:${BQ_DATASET} exists."
fi

echo -e "\n[STEPS 6, 7, 8] Parsing PDF, Cleaning Content & Creating Semantic Chunks..."
python3 "${SCRIPTS_DIR}/parse_and_chunk.py"

echo -e "\n[STEPS 9, 10, 11] Gemini Review of Chunks & Uploading Approved to US Storage..."
python3 "${SCRIPTS_DIR}/llm_review.py"

echo -e "\n[STEPS 12, 13, 14] Loading to BigQuery & Generating Embeddings via Remote Model..."
python3 "${SCRIPTS_DIR}/load_bigquery.py"

echo -e "\n[STEP 15] Running Validation and Printing Final Statistics..."
python3 "${SCRIPTS_DIR}/validate_pipeline.py"

echo -e "\n=============================================================================="
echo " PIPELINE COMPLETED SUCCESSFULLY FOR biodiversity.pdf"
echo "=============================================================================="
