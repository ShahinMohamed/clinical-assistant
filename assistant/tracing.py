"""Optional, request-local execution traces for the research workbench."""

from functools import wraps
from time import perf_counter

from safety.checks import check_answer_text


def public_value(value):
    """Snapshot graph outputs without retaining mutable Document instances."""
    if hasattr(value, "page_content") and hasattr(value, "metadata"):
        return {"excerpt": value.page_content, "metadata": public_value(value.metadata)}
    if isinstance(value, dict):
        # Internal histories duplicate earlier outputs and can grow on retries.
        return {
            str(key): public_value(item)
            for key, item in value.items()
            if key not in {"history", "initial_candidates"}
        }
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def traced_node(observer, stage, title, function):
    """Observe real node outputs; leave the existing graph contract untouched."""
    if observer is None:
        return function

    @wraps(function)
    def wrapped(state):
        started = perf_counter()
        try:
            output = function(state)
        except Exception as error:
            observer({
                "stage": stage,
                "title": title,
                "status": "error",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "output": {"error_type": type(error).__name__},
            })
            raise

        snapshot = public_value(output)
        guards = output.get("guardrail_results", output.get("input_guardrail", {}))
        blocked = output.get("blocked") or output.get("status") == "refused"
        if isinstance(guards, dict):
            blocked = blocked or guards.get("allowed") is False
            blocked = blocked or any(
                isinstance(check, dict) and check.get("allowed") is False
                for check in guards.values()
            )
        status = "completed"
        if blocked:
            status = "blocked"
        elif (
            output.get("status") == "error"
            or output.get("web_error")
            or output.get("execution", {}).get("status") == "error"
        ):
            status = "error"
        observer({
            "stage": stage,
            "title": title,
            "status": status,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "output": snapshot,
        })
        return output

    return wrapped


def redact_blocked_outputs(steps):
    """Preserve output guardrails when users inspect intermediate generations."""
    for branch in ("evidence", "cohort"):
        approved = any(
            step["stage"] == f"{branch}.guard_output"
            and step["status"] == "completed"
            and step["output"].get("guardrail_results", {}).get("output", {}).get("allowed") is True
            for step in steps
        )
        for step in steps:
            if not step["stage"].startswith(branch + "."):
                continue
            output = step["output"]
            if "answer" in output and not approved:
                output["answer"] = "Generated text withheld because it did not pass an output check."
            if "draft" in output and (
                not approved or not check_answer_text(output["draft"].get("reason"))["allowed"]
            ):
                output["draft"]["reason"] = "Explanation withheld by the output guardrail."
    return steps
