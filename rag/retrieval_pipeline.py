"""Composable retrieval features shared by chat and evaluation."""

from rag.evidence_common import (
    QUERY_PREFIX,
    get_tokenizer,
    retrieve,
)


FEATURE_HELP = {
    "hyde": "Use a hypothetical passage for vector retrieval; calls Gemini.",
    "rerank": "Rerank 20 candidates and keep five.",
    "crag": "Grade evidence and retry retrieval once; calls Gemini.",
}


def add_feature_arguments(parser):
    for name, help_text in FEATURE_HELP.items():
        parser.add_argument(
            f"--{name}",
            action="store_true",
            help=help_text,
        )


def feature_options(args):
    return {
        name: bool(getattr(args, name, False))
        for name in FEATURE_HELP
    }


def variant_name(options):
    enabled = [
        name
        for name in FEATURE_HELP
        if options.get(name)
    ]
    return "_".join(["hybrid", *enabled])


def build_pipeline(
    store,
    *,
    hyde=False,
    rerank=False,
    crag=False,
):
    if hyde:
        from rag.hyde import get_hyde_chain

        # Construct the client before per-question timing.
        get_hyde_chain()

    def search(query, original_question, initial_candidates=None):
        hypothesis = None

        if hyde:
            from rag.hyde import generate_hypothesis

            hypothesis = generate_hypothesis(query)

            candidates = retrieve(
                store,
                query,
                top_k=20,
                hyde=True,
                hypothetical_text=hypothesis,
            )

        elif initial_candidates is not None:
            # Evaluation may reuse plain hybrid candidates.
            candidates = list(initial_candidates[:20])

        else:
            candidates = retrieve(
                store,
                query,
                top_k=20,
            )

        if rerank:
            from rag.rerank import rerank_documents

            # Always rank against the user's original question,
            # including after a CRAG query rewrite.
            documents = rerank_documents(
                original_question,
                candidates,
                top_k=5,
            )
        else:
            documents = candidates[:5]

        return {
            "documents": documents,
            "trace": {
                "query": query,
                "candidate_count": len(candidates),
                "candidate_sections": [
                    document.metadata["section_id"]
                    for document in candidates
                ],
                "selected_sections": [
                    document.metadata["section_id"]
                    for document in documents
                ],
                "hypothetical_text": hypothesis,
                "hypothesis_is_evidence": False,
            },
        }

    graph = None

    if crag:
        from rag.crag import build_crag_graph

        # CRAG retries call the same search function,
        # preserving HyDE and reranking.
        graph = build_crag_graph(
            store,
            retrieval_fn=search,
        )

    def run(question, initial_candidates=None):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Question cannot be empty.")

        question = question.strip()

        tokens = get_tokenizer().encode(
            QUERY_PREFIX + question,
            truncation=False,
        )
        if len(tokens) > 512:
            raise ValueError("Question exceeds the embedding limit.")

        initial = search(
            question,
            question,
            initial_candidates=initial_candidates,
        )

        if crag:
            from rag.crag import crag_trace, run_crag

            state = run_crag(
                graph,
                question,
                initial_result=initial,
            )

            return {
                "question": question,
                "documents": state["documents"],
                "retrieval_steps": state["retrieval_steps"],
                "crag": crag_trace(state),
            }

        return {
            "question": question,
            "documents": initial["documents"],
            "retrieval_steps": [initial["trace"]],
            "crag": None,
        }

    return run