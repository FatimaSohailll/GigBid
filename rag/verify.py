from typing import Any


def verify_claims(draft: str, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # TODO: Split the draft into atomic claims and label each:
    # supported, partly_supported, or unsupported.
    # Remove or rewrite unsupported claims before the Critic sees the draft.
    raise NotImplementedError
