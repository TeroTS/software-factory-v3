# Define and validate the fixed review output contract; invalid evidence never authorizes publication.
import json

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["axis", "location", "problem", "correction"],
                "properties": {
                    "axis": {"type": "string", "enum": ["Standards", "Spec"]},
                    "location": {"type": "string", "minLength": 1},
                    "problem": {"type": "string", "minLength": 1},
                    "correction": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


# Distinguish unreliable review execution/evidence from actionable required corrections.
class ReviewError(ValueError):
    pass


# A validated rejection stops this slice; repair is not yet authorized.
class ReviewRejected(ValueError):
    pass


# Read this invocation's result file; validate the fixed schema and verdict consistency before returning findings.
def read_review_result(result):
    try:
        review = json.loads(result.read_text())
    except (OSError, ValueError) as error:
        raise ReviewError("Missing or malformed review result") from error
    if not isinstance(review, dict) or set(review) != {"verdict", "findings"}:
        raise ReviewError("Invalid review structure")
    verdict, findings = review["verdict"], review["findings"]
    if verdict not in ("PASS", "FAIL") or not isinstance(findings, list):
        raise ReviewError("Invalid review verdict/findings")
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or set(finding) != {"axis", "location", "problem", "correction"}
            or finding["axis"] not in ("Standards", "Spec")
            or any(
                not isinstance(value, str) or not value.strip()
                for value in finding.values()
            )
        ):
            raise ReviewError("Invalid required finding")
    if (verdict == "PASS") != (not findings):
        raise ReviewError("Contradictory review verdict/findings")
    return verdict, findings
