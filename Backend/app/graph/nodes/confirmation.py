from __future__ import annotations


def user_confirmed_text(
    request: str,
    *,
    positive_signals: tuple[str, ...],
    negative_signals: tuple[str, ...],
) -> bool:
    answer_text = extract_confirmation_answer(request)
    normalized = answer_text.replace(" ", "")
    return any(signal in normalized for signal in positive_signals) and not any(
        signal in normalized for signal in negative_signals
    )


def extract_confirmation_answer(request: str) -> str:
    answer_lines: list[str] = []
    for line in request.splitlines():
        stripped = line.strip()
        if "回答：" in stripped:
            answer_lines.append(stripped.split("回答：", 1)[1].strip())
        elif "回答:" in stripped:
            answer_lines.append(stripped.split("回答:", 1)[1].strip())

    return "\n".join(answer_lines).strip() or request
