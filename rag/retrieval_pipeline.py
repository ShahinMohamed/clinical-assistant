"""One graph for composable evidence retrieval and answer generation."""

from typing import TypedDict

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.graph import END, START, StateGraph

from rag.evidence_common import (
    QUERY_PREFIX,
    get_embeddings,
    get_tokenizer,
    retrieve,
)


CANDIDATE_K = 20
FINAL_K = 5
MAX_WEB_CHUNKS_PER_SOURCE = 80

FEATURE_HELP = {
    "hyde": "Use a hypothetical passage for local vector retrieval.",
    "rerank": "Rerank local and web candidates in the shared graph node.",
    "crag": "Check evidence support before answering.",
    "web": "Allow one live web fallback; requires --crag.",
}


class State(TypedDict, total=False):
    question: str
    hypothesis: str | None
    initial_candidates: list[Document] | None
    candidates: list[Document]
    web_documents: list[Document]
    documents: list[Document]
    local_documents: list[Document]
    origin: str
    pending_trace: dict
    retrieval_steps: list[dict]
    attempts: int
    sufficient: bool
    supporting_numbers: list[int]
    checks: list[dict]
    web_used: bool
    web_error: str | None
    answer: str


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
    return "_".join([
        "hybrid",
        *[
            name
            for name in FEATURE_HELP
            if options.get(name)
        ],
    ])


def candidate_trace(trace, candidates):
    return {
        **trace,
        "candidate_count": len(candidates),
        "candidate_sections": [
            document.metadata["section_id"]
            for document in candidates
        ],
        "candidate_urls": sorted({
            document.metadata["canonical_url"]
            for document in candidates
        }),
    }


def build_pipeline(
    store,
    *,
    hyde=False,
    rerank=False,
    crag=False,
    web=False,
    answer_fn=None,
):
    """
    answer_fn receives graph state and returns answer text.
    Evaluation omits answer_fn, so generation is skipped.
    """

    if web and not crag:
        raise ValueError("--web requires --crag.")

    grader = None

    if crag:
        from rag.crag import get_grader

        grader = get_grader()

    if hyde:
        from rag.hyde import get_hyde_chain

        get_hyde_chain()

    # ---------- Graph nodes ----------

    def hyde_node(state):
        from rag.hyde import generate_hypothesis

        return {
            "hypothesis": generate_hypothesis(state["question"]),
        }

    def retrieve_local_node(state):
        if not hyde and state["initial_candidates"] is not None:
            candidates = list(
                state["initial_candidates"][:CANDIDATE_K]
            )
        else:
            candidates = retrieve(
                store,
                state["question"],
                top_k=CANDIDATE_K,
                hyde=hyde,
                hypothetical_text=state["hypothesis"],
                rerank=False,
            )

        trace = candidate_trace(
            {
                "origin": "local",
                "query": state["question"],
                "hypothetical_text": state["hypothesis"],
                "hypothesis_is_evidence": False,
            },
            candidates,
        )

        return {
            "origin": "local",
            "candidates": candidates,
            "pending_trace": trace,
            "attempts": 1,
        }

    def retrieve_web_node(state):
        from rag.web_evidence import retrieve_web

        error_name = None

        try:
            result = retrieve_web(state["question"])
            documents = result["documents"]
            trace = result["trace"]

        except Exception as error:
            error_name = type(error).__name__
            documents = []
            trace = {
                "origin": "web",
                "mode": "live",
                "query": state["question"],
                "sources": [],
                "skipped_sources": [],
                "error": error_name,
            }

        return {
            "origin": "web",
            "web_documents": documents,
            "candidates": [],
            "pending_trace": trace,
            "web_used": True,
            "web_error": error_name,
            "attempts": 2,
        }

    def chunk_web_node(state):
        splitter = (
            RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
                get_tokenizer(),
                chunk_size=350,
                chunk_overlap=50,
            )
        )

        chunks = []
        trace = dict(state["pending_trace"])
        skipped = list(trace.get("skipped_sources", []))

        for document in state["web_documents"]:
            source_chunks = splitter.split_documents([document])

            too_large = len(source_chunks) > MAX_WEB_CHUNKS_PER_SOURCE
            too_long = any(
                len(get_tokenizer().encode(
                    chunk.page_content,
                    truncation=False,
                )) > 512
                for chunk in source_chunks
            )

            if too_large or too_long:
                skipped.append({
                    "url": document.metadata["canonical_url"],
                    "error": "ChunkLimitExceeded",
                })
                continue

            for number, chunk in enumerate(source_chunks, start=1):
                metadata = chunk.metadata

                metadata["section_id"] = (
                    f"{metadata['source_id']}/"
                    f"{metadata['checksum'][:12]}/chunk-{number}"
                )

            chunks.extend(source_chunks)

        trace.update({
            "extracted_chunk_count": len(chunks),
            "skipped_sources": skipped,
        })

        return {
            "candidates": chunks,
            "pending_trace": trace,
        }

    def rank_web_node(state):
        chunks = state["candidates"]
        candidates = []

        if chunks:
            embeddings = get_embeddings()
            query_vector = embeddings.embed_query(state["question"])

            vectors = embeddings.embed_documents([
                document.page_content
                for document in chunks
            ])

            # Existing embedding configuration normalizes vectors.
            scores = [
                sum(
                    left * right
                    for left, right in zip(
                        query_vector,
                        vector,
                        strict=True,
                    )
                )
                for vector in vectors
            ]

            order = sorted(
                range(len(chunks)),
                key=lambda index: (-scores[index], index),
            )

            candidates = [
                chunks[index]
                for index in order[:CANDIDATE_K]
            ]

        return {
            "candidates": candidates,
            "pending_trace": candidate_trace(
                {
                    **state["pending_trace"],
                    "candidate_ranking": "embedding_similarity",
                },
                candidates,
            ),
        }

    def rerank_node(state):
        from rag.rerank import rerank_documents

        # Shared by both retrieval paths.
        # Hypothetical text is never used as the reranking question.
        return {
            "candidates": rerank_documents(
                state["question"],
                state["candidates"],
                top_k=FINAL_K,
            ),
        }

    def select_node(state):
        selected = state["candidates"][:FINAL_K]

        trace = {
            **state["pending_trace"],
            "reranking_enabled": rerank,
            "selected_sections": [
                document.metadata["section_id"]
                for document in selected
            ],
        }

        update = {
            "retrieval_steps": state["retrieval_steps"] + [trace],
        }

        if state["origin"] == "local":
            update["documents"] = selected
            update["local_documents"] = selected

        else:
            # Recheck combined local + web evidence.
            # With no usable web passages, abstain rather than repeating
            # the already-failed local support check.
            update["documents"] = (
                state["local_documents"] + selected
                if selected
                else []
            )

        return update

    def grade_node(state):
        from rag.crag import grade_documents

        result = grade_documents(
            grader,
            state["question"],
            state["documents"],
            state["attempts"],
        )

        return {
            "sufficient": result["sufficient"],
            "supporting_numbers": result["supporting_numbers"],
            "checks": state["checks"] + [result["check"]],
        }

    def finish_node(state):
        if not crag:
            return {"documents": state["documents"]}

        return {
            "documents": [
                state["documents"][number - 1]
                for number in state["supporting_numbers"]
            ],
        }

    def generate_node(state):
        if state["documents"]:
            return {"answer": answer_fn(state)}

        if state["web_error"]:
            message = (
                "Local evidence was insufficient, and the web fallback "
                "encountered an operational error."
            )
        elif state["checks"]:
            message = state["checks"][-1]["reason"]
        else:
            message = "No usable passages were retrieved."

        return {
            "answer": f"INSUFFICIENT_EVIDENCE: {message}",
        }

    # ---------- Conditional routing ----------

    def route_start(state):
        return "hyde" if hyde else "retrieve_local"

    def route_candidates(state):
        return "rerank" if rerank else "select"

    def route_selected(state):
        return "grade" if crag else "finish"

    def route_grade(state):
        if state["sufficient"]:
            return "finish"

        if web and not state["web_used"]:
            return "retrieve_web"

        return "finish"

    # ---------- Build the single graph ----------

    graph = StateGraph(State)

    nodes = {
        "hyde": hyde_node,
        "retrieve_local": retrieve_local_node,
        "retrieve_web": retrieve_web_node,
        "chunk_web": chunk_web_node,
        "rank_web": rank_web_node,
        "rerank": rerank_node,
        "select": select_node,
        "grade": grade_node,
        "finish": finish_node,
    }

    for name, function in nodes.items():
        graph.add_node(name, function)

    graph.add_conditional_edges(
        START,
        route_start,
        {"hyde": "hyde", "retrieve_local": "retrieve_local"},
    )

    graph.add_edge("hyde", "retrieve_local")

    for name in ["retrieve_local", "rank_web"]:
        graph.add_conditional_edges(
            name,
            route_candidates,
            {"rerank": "rerank", "select": "select"},
        )

    graph.add_edge("rerank", "select")

    graph.add_conditional_edges(
        "select",
        route_selected,
        {"grade": "grade", "finish": "finish"},
    )

    graph.add_conditional_edges(
        "grade",
        route_grade,
        {"retrieve_web": "retrieve_web", "finish": "finish"},
    )

    graph.add_edge("retrieve_web", "chunk_web")
    graph.add_edge("chunk_web", "rank_web")

    if answer_fn is not None:
        graph.add_node("generate", generate_node)
        graph.add_edge("finish", "generate")
        graph.add_edge("generate", END)
    else:
        graph.add_edge("finish", END)

    compiled = graph.compile()

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

        state = compiled.invoke(
            {
                "question": question,
                "hypothesis": None,
                "initial_candidates": initial_candidates,
                "candidates": [],
                "web_documents": [],
                "documents": [],
                "local_documents": [],
                "origin": "local",
                "pending_trace": {},
                "retrieval_steps": [],
                "attempts": 0,
                "sufficient": False,
                "supporting_numbers": [],
                "checks": [],
                "web_used": False,
                "web_error": None,
            },
            config={"recursion_limit": 25},
        )

        trace = None

        if crag:
            from rag.crag import crag_trace

            trace = crag_trace(state)

        result = {
            "question": question,
            "documents": state["documents"],
            "retrieval_steps": state["retrieval_steps"],
            "crag": trace,
        }

        if answer_fn is not None:
            result["answer"] = state["answer"]

        return result

    return run