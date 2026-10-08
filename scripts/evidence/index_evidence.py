"""Build and activate an evidence index: python -m scripts.evidence.index_evidence."""

import argparse
import json
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.evidence_common import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    INDEX_SETTINGS,
    VECTOR_SIZE,
    create_engine,
    create_store,
    database_url,
    get_active_index,
    get_tokenizer,
    hybrid_config,
    load_documents,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Build another index even if inputs are unchanged.",
    )
    args = parser.parse_args()

    documents, checksum = load_documents()
    active = get_active_index()

    if (
        active
        and not args.force
        and active["corpus_checksum"] == checksum
        and active["settings"] == INDEX_SETTINGS
    ):
        print("Evidence and settings are unchanged. Indexing skipped.")
        return

    tokenizer = get_tokenizer()
    splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        tokenizer,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = splitter.split_documents(documents)

    if not chunks:
        raise ValueError("No chunks were created.")

    chunk_ids = []

    for number, chunk in enumerate(chunks, start=1):
        tokens = tokenizer.encode(
            chunk.page_content,
            truncation=False,
        )
        if len(tokens) > 512:
            raise ValueError("A chunk exceeds the embedding token limit.")

        chunk_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{checksum}:{number}:{chunk.metadata['section_id']}",
            )
        )
        chunk.metadata["chunk_id"] = chunk_id
        chunk_ids.append(chunk_id)

    # New table: the currently active table is not deleted or modified.
    table_name = f"evidence_chunks_{uuid.uuid4().hex[:16]}"

    engine = create_engine()
    engine.init_vectorstore_table(
        table_name=table_name,
        vector_size=VECTOR_SIZE,
        hybrid_search_config=hybrid_config(),
    )
    store = create_store(engine, table_name)

    for start in range(0, len(chunks), 32):
        store.add_documents(
            documents=chunks[start : start + 32],
            ids=chunk_ids[start : start + 32],
        )
        print(f"Stored {min(start + 32, len(chunks))}/{len(chunks)} chunks")

    # Check storage and add a keyword-search index.
    with psycopg.connect(database_url()) as connection:
        stored_count = connection.execute(
            sql.SQL("SELECT COUNT(*) FROM public.{}").format(
                sql.Identifier(table_name)
            )
        ).fetchone()[0]

        if stored_count != len(chunks):
            raise ValueError("Stored chunk count does not match.")

        connection.execute(
            sql.SQL(
                "CREATE INDEX {} ON public.{} USING GIN (content_tsv)"
            ).format(
                sql.Identifier(f"{table_name}_fts"),
                sql.Identifier(table_name),
            )
        )

    # Structural smoke test, not clinical answer validation.
    if not store.similarity_search(
        "HbA1c",
        k=1,
        hybrid_search_config=hybrid_config(),
    ):
        raise ValueError("Retrieval smoke test returned no documents.")

    configuration = {
        "table_name": table_name,
        "settings": INDEX_SETTINGS,
        "corpus_checksum": checksum,
        "page_count": len(documents),
        "chunk_count": len(chunks),
        "indexed_at": datetime.now(UTC).isoformat(),
    }

    # Activate only after successful indexing and checks.
    with psycopg.connect(database_url()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS public.evidence_active_index (
                id BOOLEAN PRIMARY KEY CHECK (id),
                configuration JSONB NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO public.evidence_active_index (id, configuration)
            VALUES (TRUE, %s)
            ON CONFLICT (id)
            DO UPDATE SET configuration = EXCLUDED.configuration
            """,
            (Jsonb(configuration),),
        )

    print(json.dumps(configuration, indent=2))


if __name__ == "__main__":
    main()
