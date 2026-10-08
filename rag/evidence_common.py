"""Shared helpers for evidence indexing and retrieval."""

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PGEngine, PGVectorStore
from langchain_postgres.v2.hybrid_search_config import (
    HybridSearchConfig,
    reciprocal_rank_fusion,
)
from sqlalchemy.engine import make_url
from transformers import AutoTokenizer


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data/evidence/processed"

load_dotenv(ROOT / ".env")

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
VECTOR_SIZE = 384
CHUNK_SIZE = 350
CHUNK_OVERLAP = 50
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

INDEX_SETTINGS = {
    "pipeline_version": "phase6-langchain-v1",
    "model": MODEL_NAME,
    "model_revision": MODEL_REVISION,
    "vector_size": VECTOR_SIZE,
    "chunk_size": CHUNK_SIZE,
    "chunk_overlap": CHUNK_OVERLAP,
    "normalize_embeddings": True,
    "query_prefix": QUERY_PREFIX,
}


def required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def database_url():
    # Keep DATABASE_URL in the normal psycopg format:
    # postgresql://user:password@host:port/database
    return required("DATABASE_URL")


@lru_cache(maxsize=1)
def get_tokenizer():
    return AutoTokenizer.from_pretrained(
        MODEL_NAME,
        revision=MODEL_REVISION,
        trust_remote_code=False,
    )


@lru_cache(maxsize=1)
def get_embeddings():
    return HuggingFaceEmbeddings(
        model_name=MODEL_NAME,
        model_kwargs={
            "device": "cpu",
            "revision": MODEL_REVISION,
            "trust_remote_code": False,
        },
        encode_kwargs={
            "normalize_embeddings": True,
            "batch_size": 16,
            "prompt": "",
        },
        query_encode_kwargs={
            "normalize_embeddings": True,
            "prompt": QUERY_PREFIX,
        },
    )


def load_documents():
    """Load only sources listed in the current processed manifest."""
    manifest = json.loads(
        (PROCESSED / "manifest.json").read_text(encoding="utf-8")
    )

    documents = []
    section_ids = set()
    source_ids = set()
    digest = hashlib.sha256()

    metadata_fields = [
        "source_id",
        "section_id",
        "title",
        "author",
        "publisher",
        "publication_date",
        "canonical_url",
        "geographic_scope",
        "checksum",
        "pdf_page_start",
        "pdf_page_end",
    ]

    for source in manifest["sources"]:
        source_id = source["source_id"]

        if source_id in source_ids:
            raise ValueError(f"Duplicate source ID: {source_id}")
        source_ids.add(source_id)

        path = PROCESSED / f"{source_id}.json"
        content = path.read_bytes()

        digest.update(source_id.encode("utf-8") + b"\0")
        digest.update(content)

        pages = json.loads(content)

        if len(pages) != source["sections"]:
            raise ValueError(f"Page count differs from manifest: {source_id}")

        for page in pages:
            if (
                page["source_id"] != source_id
                or page["checksum"] != source["checksum"]
            ):
                raise ValueError(f"Source metadata mismatch: {source_id}")

            section_id = page["section_id"]
            if section_id in section_ids:
                raise ValueError(f"Duplicate section ID: {section_id}")
            section_ids.add(section_id)

            text = page["text"].strip()
            if not text or "\ufffd" in text:
                raise ValueError(f"Invalid extracted text: {section_id}")

            metadata = {
                field: page.get(field)
                for field in metadata_fields
            }

            for field in [
                "title",
                "publisher",
                "publication_date",
                "canonical_url",
                "geographic_scope",
                "pdf_page_start",
                "pdf_page_end",
            ]:
                if not metadata[field]:
                    raise ValueError(
                        f"Missing {field} in {section_id}"
                    )

            documents.append(
                Document(page_content=text, metadata=metadata)
            )

    if not documents:
        raise ValueError("No evidence documents found.")

    if len(documents) != manifest["total_sections"]:
        raise ValueError("Total page count differs from manifest.")

    return documents, digest.hexdigest()


def hybrid_config():
    # Return a NEW configuration for each search.
    return HybridSearchConfig(
        tsv_column="content_tsv",
        tsv_lang="pg_catalog.english",
        primary_top_k=20,
        secondary_top_k=20,
        fusion_function=reciprocal_rank_fusion,
        fusion_function_parameters={
            "rrf_k": 60,
            "fetch_top_k": 5,
        },
    )


def create_engine():
    # PGEngine expects a SQLAlchemy-style connection URL.
    url = make_url(database_url()).set(
        drivername="postgresql+psycopg"
    )
    return PGEngine.from_connection_string(
        url=url.render_as_string(hide_password=False)
    )


def create_store(engine, table_name):
    return PGVectorStore.create_sync(
        engine=engine,
        table_name=table_name,
        embedding_service=get_embeddings(),
        hybrid_search_config=hybrid_config(),
    )


def validate_table_name(table_name):
    if not re.fullmatch(r"evidence_chunks_[0-9a-f]{16}", table_name):
        raise ValueError("Unexpected evidence table name.")


def get_active_index():
    with psycopg.connect(database_url()) as connection:
        connection.execute("SET TRANSACTION READ ONLY")

        exists = connection.execute(
            "SELECT to_regclass('public.evidence_active_index')"
        ).fetchone()[0]

        if exists is None:
            return None

        row = connection.execute(
            """
            SELECT configuration
            FROM public.evidence_active_index
            WHERE id = TRUE
            """
        ).fetchone()

    return row[0] if row else None


def get_store():
    configuration = get_active_index()
    if configuration is None:
        raise ValueError("No active index. Run python -m scripts.evidence.index_evidence first.")

    _, checksum = load_documents()

    if configuration["settings"] != INDEX_SETTINGS:
        raise ValueError("Index settings changed. Reindex the evidence.")

    if configuration["corpus_checksum"] != checksum:
        raise ValueError("Processed evidence changed. Reindex the evidence.")

    validate_table_name(configuration["table_name"])

    engine = create_engine()
    store = create_store(engine, configuration["table_name"])
    return store, configuration


def retrieve(
    store,
    question,
    top_k=5,
    *,
    rerank=False,
    candidate_k=20,
):
    question = question.strip()

    if not question:
        raise ValueError("Question cannot be empty.")

    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20.")

    if rerank and not top_k <= candidate_k <= 20:
        raise ValueError("candidate_k must be between top_k and 20.")

    tokens = get_tokenizer().encode(
        QUERY_PREFIX + question,
        truncation=False,
    )
    if len(tokens) > 512:
        raise ValueError("Question is too long for the embedding model.")

    fetch_k = candidate_k if rerank else top_k

    config = hybrid_config()
    config.fts_query = question
    config.fusion_function_parameters["fetch_top_k"] = fetch_k

    documents = store.similarity_search(
        question,
        k=fetch_k,
        hybrid_search_config=config,
    )

    if not rerank:
        return documents[:top_k]

    # Lazy import: normal retrieval does not initialize the reranker.
    from rag.rerank import rerank_documents

    return rerank_documents(
        question,
        documents,
        top_k=top_k,
    )
