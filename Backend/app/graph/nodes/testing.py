from app.graph.state import ProjectState
from app.graph.subgraphs.testing import integration_test
from app.graph.subgraphs.unit_testing import unit_test


def quality_gate(state: ProjectState) -> dict:
    """Compatibility wrapper.

    The real quality gate is now part of the integration_test Testing Subgraph.
    This function remains for callers that import it directly.
    """

    passed = bool(state["test_report"].get("passed"))
    return {
        "phase": "quality_gate",
        "quality_gate_passed": passed,
        "needs_revision": not passed,
        "revision_requests": state["test_report"].get("revision_requests", []),
        "timeline": ["quality_gate"],
    }
