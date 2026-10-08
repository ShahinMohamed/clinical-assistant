"""Deterministic smoke-test guardrails, not clinical or privacy validation."""

import hashlib
import re
from pathlib import Path


INPUT_RULES = [
    (
        "instruction_override",
        r"\b(?:ignore|override|bypass|disregard)\b.{0,60}"
        r"\b(?:instructions|system prompt|guardrails|safety rules)\b",
        "Requests to override the assistant's instructions are not supported.",
    ),
    (
        "record_or_identifier",
        r"\b(?:patient name|patient id|mrn|medical record number|"
        r"dob|date of birth|phone|address)\s*[:=]"
        r'|"resourceType"\s*:\s*"(?:Patient|Bundle)"',
        "Do not paste patient records or identifying details into this prototype.",
    ),
    (
        "email_address",
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "Remove identifying contact information before asking a research question.",
    ),
    (
        "individual_records",
        r"\b(?:list|show|export|return|give me)\b.{0,60}"
        r"\bpatient\s+(?:names|ids|addresses|records)\b",
        "This assistant supports aggregate synthetic-cohort analysis, not patient lists.",
    ),
    (
        "personal_medical_request",
        r"\b(?:diagnose me|prescribe for me|what dose should i take)\b"
        r"|\bshould (?:i|my mother|my father|my child)\b.{0,60}"
        r"\b(?:take|start|stop|increase|decrease|inject)\b",
        "This assistant does not provide individual diagnosis or medication advice.",
    ),
]

PERSONAL_DIRECTIVE = (
    r"\b(?:you should|you must|you need to)\s+"
    r"(?:take|start|stop|increase|decrease|inject)\b"
    r"|\b(?:start taking|stop taking|increase your dose|decrease your dose)\b"
)


def decision(allowed, rule=None, reason=None):
    return {"allowed": allowed, "rule": rule, "reason": reason}


def guardrail_settings():
    return {
        "enabled": True,
        "implementation": "simple-rules-v1",
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "medical_fact_verification": False,
        "clinical_validation": False,
        "comprehensive_pii_detection": False,
    }


def check_input(question):
    if not isinstance(question, str) or not question.strip():
        return decision(False, "empty_input", "Ask a nonempty research question.")

    if len(question) > 2000:
        return decision(False, "input_length", "Keep questions within 2,000 characters.")

    # Inspect normalized whitespace without rewriting the actual question.
    inspected = " ".join(question.split())

    for rule, pattern, reason in INPUT_RULES:
        if re.search(pattern, inspected, re.IGNORECASE):
            return decision(False, rule, reason)

    return decision(True)


def check_answer_text(answer):
    if not isinstance(answer, str) or not answer.strip():
        return decision(False, "empty_output", "The generated answer was empty.")

    inspected = " ".join(answer.split())

    if re.search(PERSONAL_DIRECTIVE, inspected, re.IGNORECASE):
        return decision(
            False,
            "personal_directive",
            "The generated text contained a personal medication directive.",
        )

    return decision(True)


def check_evidence_output(answer, source_count):
    checked = check_answer_text(answer)
    if not checked["allowed"]:
        return checked

    if answer.strip().startswith("INSUFFICIENT_EVIDENCE:"):
        return decision(True, "abstention")

    if source_count == 0:
        return decision(False, "no_sources", "No source excerpts were available.")

    citations = {int(number) for number in re.findall(r"\[(\d+)\]", answer)}

    if not citations:
        return decision(
            False, "missing_citations", "The answer contained no citation identifiers."
        )

    if any(number < 1 or number > source_count for number in citations):
        return decision(
            False,
            "invalid_citations",
            "The answer cited an unavailable source identifier.",
        )

    # Valid identifiers do not establish that claims are supported.
    return decision(True)
