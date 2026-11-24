"""把当前需求澄清的结构化答案完整转换为模型可理解的业务问答。"""


def _answer_text(value: object, options: dict[str, str]) -> str:
    """保留文本、单选、多选及其他补充，将稳定选项值还原为业务含义。"""
    if isinstance(value, dict):
        selected = _answer_text(value.get("selected"), options)
        other = str(value.get("other") or "").strip()
        return "；".join(part for part in (selected, f"其他补充：{other}" if other else "") if part)
    if isinstance(value, list):
        return "、".join(text for item in value if (text := _answer_text(item, options)))
    text = str(value).strip() if value is not None else ""
    return "" if text == "__other__" else options.get(text, text)


def without_redundant_original_prefix(request: str) -> str:
    """折叠连续重复的原始需求前缀，使多轮恢复与澄清保持幂等。"""
    original = request.strip()
    prefix = "原始需求：\n"
    while original.startswith(prefix + prefix):
        original = original[len(prefix):]
    return original


def requirement_clarification_request(
    original_request: str, clarification: dict, answers: dict, request: str = ""
) -> str:
    """结合当前问题标题、题意和选项解释组装完整输入，原结构化答案保持不变。"""
    # 与当前前端的 question key 规则保持一致，id 优先，其次标题、问题原文及位置。
    questions = {}
    for index, question in enumerate(clarification.get("questions") or []):
        key = question.get("id") or question.get("header") or question.get("question") or str(index)
        questions[key] = question
    lines = []
    for key, value in answers.items():
        if key.startswith("__"):
            continue
        question = questions.get(key, {})
        options = {}
        for option in question.get("options") or []:
            label = str(option.get("label") or "")
            description = str(option.get("description") or "").strip()
            options[option.get("value") or label] = f"{label}（{description}）" if description else label
        answer = _answer_text(value, options)
        if not answer:
            continue
        title = str(question.get("header") or question.get("question") or key)
        prompt = str(question.get("question") or "")
        label = f"{title}（{prompt}）" if prompt and prompt != title else title
        lines.append(f"{label}：{answer}")
    original = without_redundant_original_prefix(original_request)
    # 冻结请求可能已是上一轮完整恢复信封；直接续写答案，不能把整段内容再次包成原始需求。
    parts = [original if original.startswith("原始需求：") else f"原始需求：\n{original}"] if original else []
    if request.strip():
        parts.append(f"本轮补充说明：\n{request.strip()}")
    if lines:
        parts.append("用户对本轮澄清问题的回答：\n" + "\n".join(lines))
    return "\n\n".join(parts)
