"""Semantic Vector Retrieval Engine for Environmental Books and Research Evidence."""

import copy
import logging
import sys
from typing import Any, Dict, List, Optional, Tuple
from google.cloud import bigquery
from config import BQ_DATASET, BQ_EMBEDDING_MODEL, BQ_LOCATION, PROJECT_ID

logger = logging.getLogger(__name__)


class BigQueryRetriever:
    """Performs controlled vector retrieval over BigQuery knowledge tables."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        dataset_id: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ):
        self.project_id = project_id or PROJECT_ID
        self.location = location or BQ_LOCATION
        self.dataset_id = dataset_id or BQ_DATASET
        self.embedding_model = embedding_model or BQ_EMBEDDING_MODEL
        self._client: Optional[bigquery.Client] = None
        # Separate retrieval result caches by source/tool:
        self._query_embedding_cache: Dict[str, List[float]] = {}
        self._book_retrieval_cache: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        self._research_retrieval_cache: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        self._paper_metadata_cache: Dict[str, List[Dict[str, Any]]] = {}

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = bigquery.Client(project=self.project_id, location=self.location)
        return self._client

    def get_query_embedding(self, query: str) -> List[float]:
        """Generates a text-embedding-004 vector for the query, reusing cached vector if available."""
        import time
        import hashlib
        normalized_query = query.strip()
        query_hash = hashlib.md5(normalized_query.encode()).hexdigest()[:12]

        if normalized_query in self._query_embedding_cache:
            print(f"[RETRIEVER][CACHE] cache_type=query_embedding HIT query_hash={query_hash}", file=sys.stderr)
            print(f"[CACHE] Query embedding HIT for: '{normalized_query[:50]}'", file=sys.stderr)
            return list(self._query_embedding_cache[normalized_query])

        print(f"[RETRIEVER][CACHE] cache_type=query_embedding MISS query_hash={query_hash}", file=sys.stderr)
        print(f"[CACHE] Query embedding MISS for: '{normalized_query[:50]}'", file=sys.stderr)
        print(f"[RETRIEVER][EMBEDDING][START] query_hash={query_hash} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        print(f"[RETRIEVER] Query embedding started for: '{normalized_query[:50]}'", file=sys.stderr)
        t0 = time.time()
        sql = f"""
        SELECT ml_generate_embedding_result AS vec
        FROM ML.GENERATE_EMBEDDING(
          MODEL `{self.embedding_model}`,
          (SELECT @query_text AS content)
        )
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_text", "STRING", normalized_query)
            ]
        )
        results = list(self.client.query(sql, job_config=job_config).result())
        duration = time.time() - t0
        if not results or not results[0].vec:
            raise RuntimeError(f"Failed to generate query embedding for: {normalized_query}")

        vec = [float(x) for x in results[0].vec]
        self._query_embedding_cache[normalized_query] = vec
        print(
            f"[RETRIEVER][EMBEDDING][END] query_hash={query_hash} duration_ms={duration*1000:.0f} "
            f"dimension={len(vec)} timestamp={time.strftime('%H:%M:%S')}",
            file=sys.stderr
        )
        print(f"[RETRIEVER] Query embedding completed in {duration:.3f}s (dim: {len(vec)})", file=sys.stderr)
        return list(vec)


    def search_books(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Searches book_chunks via vector cosine similarity."""
        import time
        import hashlib
        normalized_query = query.strip()
        top_k = max(1, min(int(top_k), 20))
        query_hash = hashlib.md5(normalized_query.encode()).hexdigest()[:12]
        cache_key = (normalized_query, top_k)

        print(f"[RETRIEVER][START] tool_name=search_book_knowledge query_hash={query_hash} top_k={top_k} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)

        if cache_key in self._book_retrieval_cache:
            print(f"[RETRIEVER][CACHE] cache_type=book_retrieval HIT query_hash={query_hash} top_k={top_k} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
            print(f"[CACHE] Book retrieval HIT for: '{normalized_query[:50]}' (top_k={top_k})", file=sys.stderr)
            results = copy.deepcopy(self._book_retrieval_cache[cache_key])
            print(f"[RETRIEVER][RETURN] tool_name=search_book_knowledge result_count={len(results)} source=BOOK timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
            return results

        print(f"[RETRIEVER][CACHE] cache_type=book_retrieval MISS query_hash={query_hash} top_k={top_k} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        print(f"[CACHE] Book retrieval MISS for: '{normalized_query[:50]}' (top_k={top_k})", file=sys.stderr)
        print(f"[RETRIEVER] Starting BigQuery book search for: '{normalized_query[:50]}'", file=sys.stderr)
        t_start = time.time()
        query_vector = self.get_query_embedding(normalized_query)

        print(f"[RETRIEVER][VECTOR_SEARCH][START] source=BOOK tables=[book_embeddings, book_chunks] timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        print(f"[RETRIEVER] Vector search started in BigQuery (table: book_embeddings)...", file=sys.stderr)
        t_bq = time.time()

        sql = f"""
        SELECT 
          b.chunk_id,
          b.book_id,
          b.domain,
          b.chapter,
          b.section,
          b.page_start,
          b.page_end,
          b.content,
          b.topics,
          b.source_uri,
          ML.DISTANCE(e.embedding, @query_vector, 'COSINE') AS cosine_distance,
          ROUND(1.0 - ML.DISTANCE(e.embedding, @query_vector, 'COSINE'), 4) AS cosine_similarity
        FROM `{self.dataset_id}.book_embeddings` e
        JOIN `{self.dataset_id}.book_chunks` b ON e.chunk_id = b.chunk_id
        ORDER BY cosine_distance ASC
        LIMIT @top_k
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vector),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
            ]
        )
        query_job = self.client.query(sql, job_config=job_config)
        results = []
        for row in query_job.result():
            results.append({
                "source_type": "internal_book_knowledge",
                "chunk_id": row.chunk_id,
                "book_id": row.book_id,
                "domain": row.domain,
                "chapter": row.chapter,
                "section": row.section,
                "page_start": row.page_start,
                "page_end": row.page_end,
                "content": row.content,
                "topics": list(row.topics) if row.topics else [],
                "source_uri": row.source_uri,
                "cosine_distance": round(float(row.cosine_distance), 4),
                "similarity_score": float(row.cosine_similarity),
            })
        t_done = time.time()
        print(f"[RETRIEVER][VECTOR_SEARCH][END] source=BOOK duration_ms={(t_done - t_bq)*1000:.0f} result_count={len(results)} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        print(f"[RETRIEVER] BigQuery query completed in {t_done - t_bq:.3f}s (total search time: {t_done - t_start:.3f}s, rows: {len(results)})", file=sys.stderr)
        self._book_retrieval_cache[cache_key] = results
        print(f"[RETRIEVER][RETURN] tool_name=search_book_knowledge result_count={len(results)} source=BOOK timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        return copy.deepcopy(results)


    def search_research(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Searches research_evidence via vector cosine similarity."""
        import time
        import hashlib
        normalized_query = query.strip()
        top_k = max(1, min(int(top_k), 20))
        query_hash = hashlib.md5(normalized_query.encode()).hexdigest()[:12]
        cache_key = (normalized_query, top_k)

        print(f"[RETRIEVER][START] tool_name=search_research_evidence query_hash={query_hash} top_k={top_k} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)

        if cache_key in self._research_retrieval_cache:
            print(f"[RETRIEVER][CACHE] cache_type=research_retrieval HIT query_hash={query_hash} top_k={top_k} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
            print(f"[CACHE] Research retrieval HIT for: '{normalized_query[:50]}' (top_k={top_k})", file=sys.stderr)
            results = copy.deepcopy(self._research_retrieval_cache[cache_key])
            print(f"[RETRIEVER][RETURN] tool_name=search_research_evidence result_count={len(results)} source=RESEARCH timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
            return results

        print(f"[RETRIEVER][CACHE] cache_type=research_retrieval MISS query_hash={query_hash} top_k={top_k} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        print(f"[CACHE] Research retrieval MISS for: '{normalized_query[:50]}' (top_k={top_k})", file=sys.stderr)
        print(f"[RETRIEVER] Starting BigQuery research search for: '{normalized_query[:50]}'", file=sys.stderr)
        t_start = time.time()
        query_vector = self.get_query_embedding(normalized_query)

        print(f"[RETRIEVER][VECTOR_SEARCH][START] source=RESEARCH tables=[research_embeddings, research_evidence, research_papers] timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        print(f"[RETRIEVER] Vector search started in BigQuery (table: research_embeddings)...", file=sys.stderr)
        t_bq = time.time()
        sql = f"""
        SELECT 
          e.evidence_id,
          r.paper_id,
          p.title AS paper_title,
          p.journal,
          p.year,
          r.section,
          r.page_start,
          r.page_end,
          r.evidence_text,
          r.variables,
          r.relationship,
          r.direction,
          r.outcome,
          r.magnitude,
          r.study_location,
          r.study_period,
          r.study_design,
          r.measurement_method,
          r.statistical_method,
          r.limitations,
          r.topics,
          r.source_uri,
          ML.DISTANCE(e.embedding, @query_vector, 'COSINE') AS cosine_distance,
          ROUND(1.0 - ML.DISTANCE(e.embedding, @query_vector, 'COSINE'), 4) AS cosine_similarity
        FROM `{self.dataset_id}.research_embeddings` e
        JOIN `{self.dataset_id}.research_evidence` r ON e.evidence_id = r.evidence_id
        JOIN `{self.dataset_id}.research_papers` p ON r.paper_id = p.paper_id
        ORDER BY cosine_distance ASC
        LIMIT @top_k
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vector),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
            ]
        )
        query_job = self.client.query(sql, job_config=job_config)
        results = []
        for row in query_job.result():
            results.append({
                "source_type": "internal_research_paper_evidence",
                "evidence_id": row.evidence_id,
                "paper_id": row.paper_id,
                "paper_title": row.paper_title,
                "title": row.paper_title,
                "journal": row.journal,
                "year": row.year,
                "section": row.section,
                "page_start": row.page_start,
                "page_end": row.page_end,
                "evidence_text": row.evidence_text,
                "variables": list(row.variables) if row.variables else [],
                "relationship": row.relationship,
                "direction": row.direction,
                "outcome": row.outcome,
                "magnitude": row.magnitude,
                "study_location": row.study_location,
                "study_period": row.study_period,
                "study_design": row.study_design,
                "measurement_method": row.measurement_method,
                "statistical_method": row.statistical_method,
                "limitations": list(row.limitations) if row.limitations else [],
                "topics": list(row.topics) if row.topics else [],
                "source_uri": row.source_uri,
                "cosine_distance": round(float(row.cosine_distance), 4),
                "similarity_score": float(row.cosine_similarity),
            })
        t_done = time.time()
        print(f"[RETRIEVER][VECTOR_SEARCH][END] source=RESEARCH duration_ms={(t_done - t_bq)*1000:.0f} result_count={len(results)} timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        print(f"[RETRIEVER] BigQuery query completed in {t_done - t_bq:.3f}s (total search time: {t_done - t_start:.3f}s, rows: {len(results)})", file=sys.stderr)
        self._research_retrieval_cache[cache_key] = results
        print(f"[RETRIEVER][RETURN] tool_name=search_research_evidence result_count={len(results)} source=RESEARCH timestamp={time.strftime('%H:%M:%S')}", file=sys.stderr)
        return copy.deepcopy(results)



    def get_paper_metadata(self, paper_id_or_title: str) -> List[Dict[str, Any]]:
        """Retrieves paper-level metadata and associated evidence counts."""
        import time
        normalized_key = paper_id_or_title.strip().lower()
        if normalized_key in self._paper_metadata_cache:
            print(f"[CACHE] Paper metadata HIT for: '{normalized_key}'", file=sys.stderr)
            return copy.deepcopy(self._paper_metadata_cache[normalized_key])

        print(f"[CACHE] Paper metadata MISS for: '{normalized_key}'", file=sys.stderr)
        print(f"[RETRIEVER] Starting BigQuery get_paper_metadata for: '{paper_id_or_title}'", file=sys.stderr)
        t0 = time.time()
        sql = f"""
        SELECT 
          p.paper_id,
          p.title,
          p.authors,
          p.year,
          p.journal,
          p.doi,
          p.study_type,
          p.domain,
          p.abstract,
          p.source_uri,
          COUNT(DISTINCT e.evidence_id) AS total_evidence_units
        FROM `{self.dataset_id}.research_papers` p
        LEFT JOIN `{self.dataset_id}.research_evidence` e ON p.paper_id = e.paper_id
        WHERE LOWER(p.paper_id) = LOWER(@query_val)
           OR LOWER(p.title) LIKE CONCAT('%', LOWER(@query_val), '%')
        GROUP BY p.paper_id, p.title, p.authors, p.year, p.journal, p.doi, p.study_type, p.domain, p.abstract, p.source_uri
        ORDER BY p.paper_id
        LIMIT 5
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_val", "STRING", paper_id_or_title.strip()),
            ]
        )
        query_job = self.client.query(sql, job_config=job_config)
        results = []
        for row in query_job.result():
            results.append({
                "paper_id": row.paper_id,
                "title": row.title,
                "authors": list(row.authors) if row.authors else [],
                "year": row.year,
                "journal": row.journal,
                "doi": row.doi,
                "study_type": row.study_type,
                "domain": row.domain,
                "abstract": row.abstract,
                "source_uri": row.source_uri,
                "total_evidence_units": row.total_evidence_units,
            })
        print(f"[RETRIEVER] BigQuery get_paper_metadata completed in {time.time() - t0:.3f}s (rows: {len(results)})", file=sys.stderr)
        self._paper_metadata_cache[normalized_key] = results
        return copy.deepcopy(results)
