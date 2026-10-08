"""Local passage reranking."""

import math
from functools import lru_cache



MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L6-v2"
MODEL_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
MAX_LENGTH = 512
BATCH_SIZE = 16


def reranker_settings():
    return {
        "model": MODEL_NAME,
        "revision": MODEL_REVISION,
        "device": "cpu",
        "max_length": MAX_LENGTH,
        "batch_size": BATCH_SIZE,
        "overlong_pair_policy": "reject",
    }


@lru_cache(maxsize=1)
def get_reranker():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(
        MODEL_NAME,
        revision=MODEL_REVISION,
        device="cpu",
        max_length=MAX_LENGTH,
        trust_remote_code=False,
        model_kwargs={"use_safetensors": True},
    )


def rerank_documents(question, documents, top_k=5):
    question = question.strip()

    if not question:
        raise ValueError("Question cannot be empty.")

    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20.")

    if not documents:
        return []

    model = get_reranker()

    pairs = [
        (question, document.page_content)
        for document in documents
    ]

    # Prevent silent loss of passage text during model truncation.
    for question_text, passage in pairs:
        tokens = model.tokenizer(
            question_text,
            passage,
            truncation=False,
        )["input_ids"]

        if len(tokens) > MAX_LENGTH:
            raise ValueError(
                "A question–passage pair exceeds 512 tokens. "
                "Shorten the question or use smaller chunks."
            )

    scores = [
        float(score)
        for score in model.predict(
            pairs,
            batch_size=BATCH_SIZE,
            show_progress_bar=False,
        )
    ]

    if (
        len(scores) != len(documents)
        or any(not math.isfinite(score) for score in scores)
    ):
        raise ValueError("Reranker returned invalid scores.")

    # Resolve score ties using the original hybrid ranking.
    order = sorted(
        range(len(documents)),
        key=lambda index: (-scores[index], index),
    )

    results = []

    for index in order[:top_k]:
        document = documents[index]

        # Preserve document IDs, content and citation metadata.
        results.append(
            document.model_copy(
                update={
                    "metadata": {
                        **document.metadata,
                        "hybrid_rank": index + 1,
                        "rerank_score": scores[index],
                    }
                }
            )
        )

    return results
