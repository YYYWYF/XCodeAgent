from app.graph.state import ProjectState
from app.services.request_complexity import decide_request_complexity


def classify_request_complexity(state: ProjectState) -> dict:
    if state.get("request_complexity"):
        request_complexity = state["request_complexity"]
        decision = {
            "confidence": 1.0,
            "reason": "Request complexity was provided by upstream context.",
            "signals": ["upstream_override"],
        }
    else:
        result = decide_request_complexity(state["request"])
        request_complexity = result.complexity
        decision = {
            "confidence": result.confidence,
            "reason": result.reason,
            "signals": result.signals,
        }

    return {
        "phase": "classify_request_complexity",
        "request_complexity": request_complexity,
        "complexity_reason": decision["reason"],
        "complexity_decision": {
            "complexity": request_complexity,
            **decision,
        },
        "timeline": ["classify_request_complexity"],
    }
