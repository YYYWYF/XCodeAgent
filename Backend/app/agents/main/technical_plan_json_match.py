"""只忽略 JSON 字符串外的排版空白，仍要求内容及定位唯一。"""
import re

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')


def _tokens(text: str) -> list | None:
    """保留字符串和数值的精确字面量与位置，拒绝非 JSON 字符及展示行号。"""
    result, cursor = [], 0
    for match in _TOKEN.finditer(text):
        if text[cursor:match.start()].strip():
            return None
        result.append((match.group(), match.start(), match.end()))
        cursor = match.end()
    return result if not text[cursor:].strip() else None


def unique_json_fragment(source: str, fragment: str) -> str | None:
    """仅在词元序列唯一且非整文件时返回实际原文，不改变业务值或猜测旧内容。"""
    tokens, expected = _tokens(source), _tokens(fragment)
    if not tokens or not expected or len(expected) >= len(tokens):
        return None
    values = [item[0] for item in expected]
    positions = [i for i in range(len(tokens) - len(values) + 1)
                 if [item[0] for item in tokens[i:i + len(values)]] == values]
    if len(positions) != 1:
        return None
    start = positions[0]
    return source[tokens[start][1]:tokens[start + len(values) - 1][2]]
