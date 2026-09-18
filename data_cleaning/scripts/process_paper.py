#!/usr/bin/env python3
"""
Darukaa.Earth Universal Research Paper Evidence Ingestion Pipeline (V3)
- Robust JSON parsing with strict=False
- Region rotation across Vertex AI clusters
- Deterministic boilerplate stripping
- LLM Evidence Cleaning + Classification + Extraction
"""

import os
import sys
import json
import re
import time
import argparse
from datetime import datetime, timezone
import pymupdf
import tiktoken
from google import genai
from google.genai import types
from google.cloud import storage, bigquery

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'pipeline_config.json')
with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

PROJECT_ID = config['project_id']
PROCESSED_BUCKET = config['processed_bucket']
BQ_DATASET = config['bigquery_dataset']
BQ_LOCATION = config['bigquery_location']
EMBEDDING_MODEL = config['embedding_model']
GEMINI_MODEL = config['gemini_model']

enc = tiktoken.get_encoding('cl100k_base')

REGIONS = ['us-east4', 'us-central1', 'us-west1']
clients = {
    loc: genai.Client(vertexai=True, project=PROJECT_ID, location=loc)
    for loc in REGIONS
}

LIGATURE_MAP = {
    '\x15': '—', '\x1b': 'ff', '\x1c': 'fi', '\x1d': 'fl', '\x1e': 'ffi',
    '\x1f': 'ffl', '\x10': '"', '\x11': '"', '\x00': '(', '\x01': ')',
    '\xad': '-', '\xa0': ' '
}

BOILERPLATE_PATTERNS = [
    r'The authors declare no competing interest',
    r'This article is a PNAS Direct Submission',
    r'Copyright ©',
    r'distributed under Creative Commons',
    r'Although PNAS asks authors to adhere',
    r'To whom correspondence may be addressed',
    r'This article contains supporting information',
    r'Published \w+ \d+, \d{4}',
    r'contributed equally to this work',
    r'^Author affiliations?:',
    r'^Author contributions?:',
    r'^ACKNOWLEDGMENTS?\.?',
    r'^Data(?:\s+and\s+code)?\s+availability',
    r'^Code\s+availability',
    r'^Declaration\s+of\s+competing\s+interest',
    r'^Competing\s+interests?',
    r'^CRediT\s+authorship',
    r'Edited by [^;]+; received',
    r'^\d+\.\s+[A-Z]\.',
    r'^\d+\.\t[A-Z]\.',
    r'https?://doi\.org/',
    r'https?://creativecommons\.org/'
]

SECTION_REGEXES = [
    (r'^(?:\d+\.?\s+)?(?:abstract|summary|highlights)\b', 'Abstract'),
    (r'^(?:\d+\.?\s+)?(?:significance)\b', 'Significance'),
    (r'^(?:\d+\.?\s+)?(?:introduction|background)\b', 'Introduction'),
    (r'^(?:\d+\.?\s+)?(?:results\s+and\s+discussion)\b', 'Results and Discussion'),
    (r'^(?:\d+\.?\s+)?(?:results)\b', 'Results'),
    (r'^(?:\d+\.?\s+)?(?:discussion)\b', 'Discussion'),
    (r'^(?:\d+\.?\s+)?(?:limitations?(?:\s+and\s+future\s+directions|\s+of\s+the\s+study)?)\b', 'Limitations'),
    (r'^(?:\d+\.?\s+)?(?:conclusions?)\b', 'Conclusion'),
    (r'^(?:\d+\.?\s+)?(?:materials?\s+and\s+methods?|star\s+methods|methods?)\b', 'Materials and Methods'),
    (r'^(?:\d+\.?\s+)?(?:data(?:\s+and\s+code)?\s+availability|code\s+availability)\b', 'Data Availability'),
    (r'^(?:\d+\.?\s+)?(?:acknowledgements?|funding)\b', 'Acknowledgments'),
    (r'^(?:\d+\.?\s+)?(?:author\s+contributions?|credit\s+authorship)\b', 'Author Contributions'),
    (r'^(?:\d+\.?\s+)?(?:declaration\s+of\s+competing\s+interest|competing\s+interests?)\b', 'Competing Interests'),
    (r'^(?:\d+\.?\s+)?(?:references|literature\s+cited|bibliography)\b', 'References')
]

FORBIDDEN_PHRASES = [
    "Copyright",
    "Creative Commons",
    "To whom correspondence",
    "Email:",
    "Authors declare",
    "Direct Submission",
    "Author contributions:",
    "Author affiliations:",
    "CRediT authorship",
    "Declaration of competing interest"
]

def clean_text(text: str) -> str:
    for k, v in LIGATURE_MAP.items():
        text = text.replace(k, v)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def is_pure_boilerplate(text: str) -> bool:
    for pat in BOILERPLATE_PATTERNS:
        if re.search(pat, text, re.I):
            return True
    return False

def detect_section_heading(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return None
    first_line = lines[0]
    if len(first_line) > 65:
        return None
    for pat, canon in SECTION_REGEXES:
        if re.search(pat, first_line, re.I):
            return first_line.strip()
    return None

def extract_paper_metadata(doc, paper_id, source_uri):
    sample_text = doc[0].get_text() + "\n" + (doc[1].get_text()[:1500] if len(doc) > 1 else "")
    prompt = f"""Extract the scientific research paper metadata from this text as JSON:
{{
  "title": "Full exact paper title",
  "abstract": "Full abstract text",
  "authors": ["Author 1", "Author 2", ...],
  "year": integer year of publication,
  "doi": "DOI string or URL",
  "journal": "Journal name",
  "study_type": "meta-analysis" | "experimental" | "observational" | "review" | "modeling" | "other",
  "domain": "environmental science domain(s)"
}}

Paper text:
{sample_text}
"""
    for attempt in range(4):
        loc = REGIONS[attempt % len(REGIONS)]
        client = clients[loc]
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    temperature=0.0
                )
            )
            data = json.loads(resp.text, strict=False)
            now_ts = datetime.now(timezone.utc).isoformat()
            return {
                'paper_id': paper_id,
                'title': data.get('title', 'Unknown Title'),
                'abstract': data.get('abstract', ''),
                'authors': data.get('authors', []),
                'year': int(data.get('year', 2024)),
                'doi': data.get('doi', ''),
                'journal': data.get('journal', ''),
                'study_type': data.get('study_type', 'meta-analysis'),
                'domain': data.get('domain', 'Environmental Science'),
                'source_uri': source_uri,
                'created_at': now_ts
            }
        except Exception as e:
            time.sleep(2.0 * (attempt + 1))

    return {
        'paper_id': paper_id,
        'title': 'Environmental Science Research Paper',
        'abstract': '',
        'authors': [],
        'year': 2024,
        'doi': '',
        'journal': '',
        'study_type': 'meta-analysis',
        'domain': 'Environmental Science',
        'source_uri': source_uri,
        'created_at': datetime.now(timezone.utc).isoformat()
    }

def parse_and_chunk_paper(doc, paper_id, source_uri, paper_title=""):
    raw_blocks = []
    current_sec = 'Introduction'
    detected_sections = set()

    for p_idx in range(len(doc)):
        page_no = p_idx + 1
        blocks = doc[p_idx].get_text('blocks')
        for b in blocks:
            raw_text = b[4].strip()
            cleaned = clean_text(raw_text)

            # Strip running headers / footers / DOI links / page numbers / download watermarks
            if re.search(r'pnas\.org|cell\.com|nature\.com|sciencedirect\.com|Downloaded from|https?://doi\.org', cleaned):
                continue
            if re.match(r'^\d+\s+of\s+\d+$', cleaned) or re.match(r'^\d+$', cleaned):
                continue
            if len(cleaned) < 5:
                continue

            # Check if this block is a section heading
            heading_match = detect_section_heading(cleaned)
            lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
            if heading_match and len(lines) == 1:
                current_sec = heading_match
                detected_sections.add(current_sec)
                continue # Do not create empty candidate unit for standalone heading

            if heading_match and len(lines) > 1:
                current_sec = heading_match
                detected_sections.add(current_sec)

            # Check if block is title-only on Page 1
            if p_idx == 0 and paper_title and (paper_title.lower() in cleaned.lower() or cleaned.lower() in paper_title.lower()) and len(cleaned) < 250:
                raw_blocks.append({
                    'page': page_no,
                    'section': 'Front Matter',
                    'text': cleaned,
                    'is_bp': True,
                    'tokens': len(enc.encode(cleaned))
                })
                continue

            # Check if block is terminal References
            if current_sec == 'References' or re.match(r'^\d+\.\s+[A-Z]\.', cleaned) or re.match(r'^\d+\.\t[A-Z]\.', cleaned):
                current_sec = 'References'
                detected_sections.add(current_sec)
                raw_blocks.append({
                    'page': page_no,
                    'section': 'References',
                    'text': cleaned,
                    'is_bp': True,
                    'tokens': len(enc.encode(cleaned))
                })
                continue

            detected_sections.add(current_sec)

            raw_blocks.append({
                'page': page_no,
                'section': current_sec,
                'text': cleaned,
                'is_bp': is_pure_boilerplate(cleaned),
                'tokens': len(enc.encode(cleaned))
            })

    # Group into candidate semantic units
    candidate_units = []
    curr_group = []
    curr_toks = 0

    for b in raw_blocks:
        if b['is_bp']:
            if curr_group:
                comb_text = "\n\n".join([x['text'] for x in curr_group])
                candidate_units.append({
                    'section': curr_group[0]['section'],
                    'page_start': curr_group[0]['page'],
                    'page_end': curr_group[-1]['page'],
                    'text': comb_text,
                    'tokens': len(enc.encode(comb_text))
                })
                curr_group = []
                curr_toks = 0
            candidate_units.append({
                'section': b['section'],
                'page_start': b['page'],
                'page_end': b['page'],
                'text': b['text'],
                'tokens': b['tokens']
            })
            continue

        is_fig = b['text'].startswith('Fig.') or b['text'].startswith('Figure')
        sec_changed = curr_group and (b['section'] != curr_group[0]['section'])

        if curr_group and (sec_changed or is_fig or (curr_toks + b['tokens'] > 550)):
            comb_text = "\n\n".join([x['text'] for x in curr_group])
            candidate_units.append({
                'section': curr_group[0]['section'],
                'page_start': curr_group[0]['page'],
                'page_end': curr_group[-1]['page'],
                'text': comb_text,
                'tokens': len(enc.encode(comb_text))
            })
            curr_group = []
            curr_toks = 0

        curr_group.append(b)
        curr_toks += b['tokens']

        if is_fig and curr_toks >= 80:
            comb_text = "\n\n".join([x['text'] for x in curr_group])
            candidate_units.append({
                'section': b['section'],
                'page_start': curr_group[0]['page'],
                'page_end': curr_group[-1]['page'],
                'text': comb_text,
                'tokens': len(enc.encode(comb_text))
            })
            curr_group = []
            curr_toks = 0

    if curr_group:
        comb_text = "\n\n".join([x['text'] for x in curr_group])
        candidate_units.append({
            'section': curr_group[0]['section'],
            'page_start': curr_group[0]['page'],
            'page_end': curr_group[-1]['page'],
            'text': comb_text,
            'tokens': len(enc.encode(comb_text))
        })

    records = []
    for idx, u in enumerate(candidate_units, start=1):
        records.append({
            'evidence_id': f"{paper_id}_evidence_{idx:04d}",
            'paper_id': paper_id,
            'section': u['section'],
            'page_start': u['page_start'],
            'page_end': u['page_end'],
            'candidate_text': u['text'],
            'token_count': u['tokens'],
            'source_uri': source_uri
        })

    return detected_sections, records

CLEAN_EVIDENCE_PROMPT = """You are the Scientific Evidence Cleaner, Classifier, and Metadata Extractor for the Darukaa.Earth Environmental RAG system.
Evaluate each candidate evidence unit from an environmental science research paper.

CRITICAL INSTRUCTIONS:
1. EVIDENCE CLEANING:
   - Identify the substantive scientific content.
   - Remove ALL non-scientific document noise INSIDE the candidate unit (copyright notices, Creative Commons licenses, author emails, institutional affiliations, submission/editor notes, publication dates, competing-interest statements, acknowledgments/funding notes).
   - DO NOT summarize, paraphrase, or rewrite scientific findings. Preserve verbatim original scientific wording, exact numbers, and in-text citations (e.g. "(Smith et al., 2022)" or "(1, 2)").
   - If meaningful scientific content remains:
     * "keep": true
     * "cleaned_evidence_text": The exact verbatim scientific text with ALL non-scientific document noise stripped out.
   - If NO meaningful scientific content remains (e.g. unit is purely references, affiliations, acknowledgments, copyright, title-only, or navigation):
     * "keep": false
     * "cleaned_evidence_text": null

2. METADATA EXTRACTION (when keep is true):
   - content_type: "definition" | "background" | "methodology" | "dataset" | "result" | "finding" | "discussion" | "relationship" | "quantitative_result" | "table" | "figure_caption" | "conclusion" | "limitation" | "other_scientific" | "boilerplate" | "references"
   - review_reason: Clear concise explanation for keep/discard decision.
   - variables: Array of specific environmental variables directly supported by this evidence (e.g. "deforestation", "forest_conversion", "soil_organic_carbon", "soil_total_nitrogen", "soil_pH", "bacterial_diversity", "fungal_diversity", "plant_pathogens", "symbionts", "cropland", "grassland", "plantation", "temperature", "precipitation", "invertebrates", "pollinators", "wetlands"). Do NOT invent variables.
   - relationship: Concise summary of relationship between variables, or null. Do NOT turn correlation into causation ("associated with" instead of "causes").
   - direction: "positive" | "negative" | "mixed" | "none" | null.
   - outcome: Specific environmental outcome (e.g. "reduced soil carbon storage", "elevated soil pH", "loss of fungal symbionts", "reduced species richness"), or null.
   - magnitude: Explicit numerical magnitude stated in the text (e.g. "29.5% average decrease in SOC; 48.2% reduction in croplands; +0.45 pH units"), or null.
   - study_location: Geographic location or scope if mentioned, or null.
   - study_period: Time period if mentioned, or null.
   - sample_size: Sample size if mentioned, or null.
   - study_design: Study design if mentioned, or null.
   - data_source: Data source if mentioned, or null.
   - measurement_method: Measurement method if mentioned, or null.
   - statistical_method: Statistical method if mentioned, or null.
   - limitations: Array of scientific limitations or caveats explicitly mentioned in this unit, or empty array.
   - topics: Array of scientific topics.

Return a JSON array of objects, one per candidate unit:
[
  {{
    "evidence_id": "string matching EVIDENCE_ID",
    "keep": boolean,
    "cleaned_evidence_text": string or null,
    "content_type": string,
    "review_reason": string,
    "variables": [string],
    "relationship": string or null,
    "direction": string or null,
    "outcome": string or null,
    "magnitude": string or null,
    "study_location": string or null,
    "study_period": string or null,
    "sample_size": string or null,
    "study_design": string or null,
    "data_source": string or null,
    "measurement_method": string or null,
    "statistical_method": string or null,
    "limitations": [string],
    "topics": [string]
  }}
]

Candidate Units to Evaluate:
{payload}
"""

def clean_and_extract_evidence_with_llm(candidate_units, batch_size=3):
    batches = [candidate_units[i:i + batch_size] for i in range(0, len(candidate_units), batch_size)]
    reviewed = []

    for b_idx, batch in enumerate(batches, start=1):
        units_text = []
        for u in batch:
            units_text.append(f"---\nEVIDENCE_ID: {u['evidence_id']}\nSECTION: {u['section']}\nPAGE_START: {u['page_start']}\nPAGE_END: {u['page_end']}\nCONTENT:\n{u['candidate_text']}")
        payload = "\n".join(units_text)
        prompt = CLEAN_EVIDENCE_PROMPT.format(payload=payload)

        max_retries = 4
        success = False
        for attempt in range(max_retries):
            loc = REGIONS[(b_idx + attempt) % len(REGIONS)]
            client = clients[loc]
            try:
                resp = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type='application/json',
                        temperature=0.0
                    )
                )
                data = json.loads(resp.text, strict=False)
                results_map = {}
                if isinstance(data, list):
                    for item in data:
                        eid = item.get('evidence_id')
                        if eid:
                            cleaned_txt = item.get('cleaned_evidence_text')
                            keep_val = bool(item.get('keep', False))
                            if keep_val and (not cleaned_txt or len(cleaned_txt.strip()) < 20):
                                keep_val = False
                                cleaned_txt = None

                            results_map[eid] = {
                                'keep': keep_val,
                                'cleaned_evidence_text': cleaned_txt.strip() if cleaned_txt else None,
                                'content_type': item.get('content_type', 'other_scientific'),
                                'review_reason': item.get('review_reason', ''),
                                'variables': list(item.get('variables', [])),
                                'relationship': item.get('relationship'),
                                'direction': item.get('direction'),
                                'outcome': item.get('outcome'),
                                'magnitude': item.get('magnitude'),
                                'study_location': item.get('study_location'),
                                'study_period': item.get('study_period'),
                                'sample_size': item.get('sample_size'),
                                'study_design': item.get('study_design'),
                                'data_source': item.get('data_source'),
                                'measurement_method': item.get('measurement_method'),
                                'statistical_method': item.get('statistical_method'),
                                'limitations': list(item.get('limitations', [])),
                                'topics': list(item.get('topics', []))
                            }

                for u in batch:
                    if u['evidence_id'] in results_map:
                        reviewed.append({**u, **results_map[u['evidence_id']]})
                    else:
                        reviewed.append({
                            **u,
                            'keep': False,
                            'cleaned_evidence_text': None,
                            'content_type': 'boilerplate',
                            'review_reason': 'Unmatched unit in LLM response',
                            'variables': [],
                            'relationship': None,
                            'direction': None,
                            'outcome': None,
                            'magnitude': None,
                            'study_location': None,
                            'study_period': None,
                            'sample_size': None,
                            'study_design': None,
                            'data_source': None,
                            'measurement_method': None,
                            'statistical_method': None,
                            'limitations': [],
                            'topics': []
                        })
                success = True
                print(f"Batch {b_idx}/{len(batches)} evaluated & cleaned ({len(batch)} units)")
                break
            except Exception as e:
                print(f"Batch {b_idx} attempt {attempt+1} failed: {e}")
                time.sleep(2.0 * (attempt + 1))

        if not success:
            for u in batch:
                reviewed.append({
                    **u,
                    'keep': False,
                    'cleaned_evidence_text': None,
                    'content_type': 'boilerplate',
                    'review_reason': 'LLM evaluation failed after retries',
                    'variables': [],
                    'relationship': None,
                    'direction': None,
                    'outcome': None,
                    'magnitude': None,
                    'study_location': None,
                    'study_period': None,
                    'sample_size': None,
                    'study_design': None,
                    'data_source': None,
                    'measurement_method': None,
                    'statistical_method': None,
                    'limitations': [],
                    'topics': []
                })
        time.sleep(1.0)

    return reviewed

def save_and_ingest_paper(paper_metadata, reviewed_units):
    paper_id = paper_metadata['paper_id']
    scratch_dir = os.path.join(os.path.dirname(__file__), '..', 'scratch')
    os.makedirs(scratch_dir, exist_ok=True)

    meta_file = os.path.join(scratch_dir, f"{paper_id}_metadata.jsonl")
    with open(meta_file, 'w', encoding='utf-8') as f:
        f.write(json.dumps(paper_metadata, ensure_ascii=False) + '\n')

    approved = []
    for u in reviewed_units:
        if u['keep'] and u['cleaned_evidence_text']:
            cleaned_txt = u['cleaned_evidence_text']
            for phrase in FORBIDDEN_PHRASES:
                if phrase.lower() in cleaned_txt.lower():
                    lines = [l for l in cleaned_txt.splitlines() if phrase.lower() not in l.lower()]
                    cleaned_txt = "\n".join(lines).strip()

            if paper_metadata['title'] and cleaned_txt == paper_metadata['title']:
                continue

            if len(cleaned_txt) >= 20:
                approved.append({
                    **u,
                    'evidence_text': cleaned_txt
                })

    now_ts = datetime.now(timezone.utc).isoformat()
    approved_file = os.path.join(scratch_dir, f"{paper_id}_approved_evidence.jsonl")
    with open(approved_file, 'w', encoding='utf-8') as f:
        for u in approved:
            rec = {
                'evidence_id': u['evidence_id'],
                'paper_id': paper_id,
                'section': u['section'],
                'page_start': u['page_start'],
                'page_end': u['page_end'],
                'evidence_text': u['evidence_text'],
                'content_type': u['content_type'],
                'variables': u['variables'],
                'relationship': u['relationship'],
                'direction': u['direction'],
                'outcome': u['outcome'],
                'magnitude': u['magnitude'],
                'study_location': u['study_location'],
                'study_period': u['study_period'],
                'sample_size': u['sample_size'],
                'study_design': u['study_design'],
                'data_source': u['data_source'],
                'measurement_method': u['measurement_method'],
                'statistical_method': u['statistical_method'],
                'limitations': u['limitations'],
                'topics': u['topics'],
                'source_uri': u['source_uri'],
                'created_at': now_ts
            }
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    # Upload to US GCS
    storage_client = storage.Client()
    bucket = storage_client.bucket(PROCESSED_BUCKET)
    blob_name = f"papers/{paper_id}_evidence.jsonl"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(approved_file, content_type='application/jsonl')
    gcs_uri = f"gs://{PROCESSED_BUCKET}/{blob_name}"

    meta_blob_name = f"papers/{paper_id}_metadata.jsonl"
    meta_blob = bucket.blob(meta_blob_name)
    meta_blob.upload_from_filename(meta_file, content_type='application/jsonl')

    # BigQuery Ingestion (Clean state for this paper)
    bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)

    del_papers_sql = f"DELETE FROM `{PROJECT_ID}.{BQ_DATASET}.research_papers` WHERE paper_id = '{paper_id}'"
    del_evidence_sql = f"DELETE FROM `{PROJECT_ID}.{BQ_DATASET}.research_evidence` WHERE paper_id = '{paper_id}'"
    del_embed_sql = f"DELETE FROM `{PROJECT_ID}.{BQ_DATASET}.research_embeddings` WHERE evidence_id LIKE '{paper_id}_%'"
    bq_client.query(del_papers_sql).result()
    bq_client.query(del_evidence_sql).result()
    bq_client.query(del_embed_sql).result()

    job_config_meta = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )
    table_papers_id = f"{PROJECT_ID}.{BQ_DATASET}.research_papers"
    load_meta_job = bq_client.load_table_from_uri(f"gs://{PROCESSED_BUCKET}/{meta_blob_name}", table_papers_id, job_config=job_config_meta)
    load_meta_job.result()

    job_config_evidence = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )
    table_evidence_id = f"{PROJECT_ID}.{BQ_DATASET}.research_evidence"
    load_evidence_job = bq_client.load_table_from_uri(gcs_uri, table_evidence_id, job_config=job_config_evidence)
    load_evidence_job.result()

    embed_sql = f"""
    INSERT INTO `{PROJECT_ID}.{BQ_DATASET}.research_embeddings`
    SELECT
      embed.evidence_id,
      embed.ml_generate_embedding_result AS embedding
    FROM ML.GENERATE_EMBEDDING(
      MODEL `{PROJECT_ID}.{BQ_DATASET}.embedding_model`,
      (SELECT evidence_id, evidence_text AS content FROM `{PROJECT_ID}.{BQ_DATASET}.research_evidence` WHERE paper_id = '{paper_id}'),
      STRUCT('RETRIEVAL_DOCUMENT' AS task_type)
    ) AS embed
    WHERE embed.ml_generate_embedding_status = '';
    """
    bq_client.query(embed_sql).result()

    v_sql = f"""
    SELECT
      (SELECT count(*) FROM `{PROJECT_ID}.{BQ_DATASET}.research_papers` WHERE paper_id = '{paper_id}') as papers_count,
      (SELECT count(*) FROM `{PROJECT_ID}.{BQ_DATASET}.research_evidence` WHERE paper_id = '{paper_id}') as evidence_count,
      (SELECT count(*) FROM `{PROJECT_ID}.{BQ_DATASET}.research_embeddings` WHERE evidence_id LIKE '{paper_id}_%') as embed_count,
      (SELECT ANY_VALUE(ARRAY_LENGTH(embedding)) FROM `{PROJECT_ID}.{BQ_DATASET}.research_embeddings` WHERE evidence_id LIKE '{paper_id}_%') as dim,
      (SELECT count(*) - count(distinct evidence_id) FROM `{PROJECT_ID}.{BQ_DATASET}.research_evidence` WHERE paper_id = '{paper_id}') as dup_evidence,
      (SELECT count(*) - count(distinct evidence_id) FROM `{PROJECT_ID}.{BQ_DATASET}.research_embeddings` WHERE evidence_id LIKE '{paper_id}_%') as dup_embeds,
      (SELECT countif(embedding is null or ARRAY_LENGTH(embedding) = 0) FROM `{PROJECT_ID}.{BQ_DATASET}.research_embeddings` WHERE evidence_id LIKE '{paper_id}_%') as failed_embeds
    """
    v_res = list(bq_client.query(v_sql).result())[0]

    return gcs_uri, v_res, approved

def main():
    parser = argparse.ArgumentParser(description="Process a research paper with LLM Evidence Cleaning")
    parser.add_argument("--pdf", required=True, help="Local PDF path")
    parser.add_argument("--paper-id", required=True, help="Unique Paper ID (e.g. paper_02)")
    parser.add_argument("--source-uri", required=True, help="Source GCS URI in Mumbai bucket")
    args = parser.parse_args()

    print("\n=======================================================")
    print(f"PROCESSING RESEARCH PAPER: {args.paper_id}")
    print(f"PDF PATH:   {args.pdf}")
    print(f"SOURCE URI: {args.source_uri}")
    print("=======================================================")

    doc = pymupdf.open(args.pdf)
    page_count = len(doc)
    print(f"Page Count: {page_count}")

    print("Extracting paper metadata with Gemini 2.5 Flash...")
    metadata = extract_paper_metadata(doc, args.paper_id, args.source_uri)
    print(f"Title:   {metadata['title']}")
    print(f"Journal: {metadata['journal']} ({metadata['year']})")
    print(f"Authors: {', '.join(metadata['authors'][:4])}...")
    print(f"DOI:     {metadata['doi']}")

    print("\nParsing document & detecting sections...")
    detected_sections, candidate_units = parse_and_chunk_paper(doc, args.paper_id, args.source_uri, metadata['title'])
    print(f"Detected {len(detected_sections)} distinct sections: {sorted(detected_sections)}")
    print(f"Generated {len(candidate_units)} candidate semantic units.")

    print("\nCleaning & Extracting evidence with Gemini 2.5 Flash...")
    reviewed_units = clean_and_extract_evidence_with_llm(candidate_units, batch_size=3)

    retained = [u for u in reviewed_units if u['keep'] and u.get('cleaned_evidence_text')]
    discarded = [u for u in reviewed_units if not u['keep'] or not u.get('cleaned_evidence_text')]

    discard_categories = {}
    for d in discarded:
        cat = d.get('content_type', 'boilerplate')
        discard_categories[cat] = discard_categories.get(cat, 0) + 1

    print(f"\nReview Complete: KEEP={len(retained)}, DISCARD={len(discarded)}")
    print(f"Discard categories: {discard_categories}")

    print("\nIngesting into US Cloud Storage & BigQuery...")
    gcs_uri, bq_stats, approved_units = save_and_ingest_paper(metadata, reviewed_units)

    print("\n=======================================================")
    print(f"PAPER VALIDATION SUMMARY: {args.paper_id}")
    print("=======================================================")
    print(f"PDF Filename:                 {os.path.basename(args.pdf)}")
    print(f"Paper Title:                  {metadata['title']}")
    print(f"Original Page Count:          {page_count}")
    print(f"Candidate Evidence Units:     {len(candidate_units)}")
    print(f"KEEP Count:                   {len(retained)}")
    print(f"DISCARD Count:                {len(discarded)}")
    print(f"Discard Categories:           {discard_categories}")
    print(f"Final research_papers Rows:   {bq_stats['papers_count']}")
    print(f"Final research_evidence Rows: {bq_stats['evidence_count']}")
    print(f"Final Embedding Rows:         {bq_stats['embed_count']}")
    print(f"Embedding Dimensionality:     {bq_stats['dim']}")
    print(f"Duplicate Evidence Count:     {bq_stats['dup_evidence']}")
    print(f"Duplicate Embeddings Count:   {bq_stats['dup_embeds']}")
    print(f"Embedding Failures:           {bq_stats['failed_embeds']}")
    print(f"Processed Evidence GCS Path:  {gcs_uri}")

    out_report = {
        'metadata': metadata,
        'page_count': page_count,
        'detected_sections': sorted(list(detected_sections)),
        'candidate_count': len(candidate_units),
        'keep_count': len(retained),
        'discard_count': len(discarded),
        'discard_categories': discard_categories,
        'bq_stats': dict(bq_stats),
        'retained_samples': approved_units[:10],
        'discarded_samples': discarded[:5]
    }
    report_file = os.path.join(os.path.dirname(__file__), '..', 'scratch', f"{args.paper_id}_validation_report.json")
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(out_report, f, ensure_ascii=False, indent=2)
    print(f"\nSaved detailed validation report to {report_file}")

if __name__ == '__main__':
    main()
