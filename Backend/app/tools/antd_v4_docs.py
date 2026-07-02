from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_DOCS_DIR = Path("/Users/yifei/Documents/antd-components")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*|[\u4e00-\u9fff]{2,}")


@dataclass(frozen=True)
class SearchResult:
    id: str
    score: int
    title: str
    kind: str
    component_slug: str
    component: str
    import_text: str
    text: str


def docs_root() -> Path:
    return Path(os.getenv("ANTD_V4_DOCS_DIR", str(DEFAULT_DOCS_DIR))).expanduser()


def is_available() -> bool:
    root = docs_root()
    return (root / "manifest.json").is_file() and (root / "chunks.jsonl").is_file()


@lru_cache(maxsize=1)
def load_manifest() -> Dict[str, Any]:
    return _read_json(docs_root() / "manifest.json")


@lru_cache(maxsize=1)
def load_chunks() -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    chunks_file = docs_root() / "chunks.jsonl"
    with chunks_file.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def list_components() -> List[Dict[str, Any]]:
    manifest = load_manifest()
    return list(manifest.get("components", []))


def get_component_doc(slug: str) -> Dict[str, Any]:
    component = _component_by_slug(slug)
    if not component:
        raise KeyError(f"Unknown antd v4 component: {slug}")

    file_path = docs_root() / str(component["file"])
    return {
        "component": component,
        "markdown": file_path.read_text(encoding="utf-8"),
    }


def search(query: str, *, limit: int = 5) -> List[SearchResult]:
    if not is_available():
        return []

    normalized_query = _normalize(query)
    query_terms = _query_terms(query)
    if not normalized_query or not query_terms:
        return []

    aliases = _component_aliases()
    results: List[SearchResult] = []
    for chunk in load_chunks():
        score = _score_chunk(chunk, normalized_query, query_terms, aliases)
        if score <= 0:
            continue
        results.append(
            SearchResult(
                id=str(chunk.get("id", "")),
                score=score,
                title=str(chunk.get("title", "")),
                kind=str(chunk.get("kind", "")),
                component_slug=str(chunk.get("componentSlug", "")),
                component=str(chunk.get("component", "")),
                import_text=str(chunk.get("importText", "")),
                text=str(chunk.get("text", "")),
            )
        )

    results.sort(key=lambda item: item.score, reverse=True)
    return results[:limit]


def search_result_to_dict(
    result: SearchResult,
    *,
    max_text_chars: Optional[int] = None,
) -> Dict[str, object]:
    text = result.text
    if max_text_chars is not None:
        text = _truncate(text, max_text_chars)

    return {
        "id": result.id,
        "score": result.score,
        "title": result.title,
        "kind": result.kind,
        "component_slug": result.component_slug,
        "component": result.component,
        "import_text": result.import_text,
        "text": text,
    }


def build_prompt_context(query: str, *, limit: int = 4, max_chars: int = 7000) -> str:
    results = search(query, limit=limit)
    if not results:
        return ""

    sections = [
        "# Internal Tool Result: antd_v4_docs",
        (
            "The following snippets come from the local offline Ant Design v4.24.16 "
            "component documentation. Treat them as authoritative for antd v4 APIs and examples. "
            "Do not use antd v5/v6 APIs when these snippets apply."
        ),
    ]
    used_chars = sum(len(section) for section in sections)
    for result in results:
        snippet = _truncate(result.text, 1600)
        block = (
            f"## {result.component} / {result.title}\n"
            f"- kind: {result.kind}\n"
            f"- import: `{result.import_text}`\n"
            f"- score: {result.score}\n\n"
            f"{snippet}"
        )
        if used_chars + len(block) > max_chars:
            break
        sections.append(block)
        used_chars += len(block)

    return "\n\n---\n\n".join(sections)


def _read_json(file_path: Path) -> Dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    return value if isinstance(value, dict) else {}


def _component_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    normalized_slug = slug.strip().lower()
    for component in list_components():
        if str(component.get("slug", "")).lower() == normalized_slug:
            return component
    return None


@lru_cache(maxsize=1)
def _component_aliases() -> Dict[str, List[str]]:
    aliases: Dict[str, List[str]] = {}
    for component in list_components():
        slug = str(component.get("slug", ""))
        values = [
            slug,
            str(component.get("title", "")),
            str(component.get("codeName", "")),
            str(component.get("importName", "")),
            str(component.get("subtitle", "")),
            str(component.get("descriptionZh", "")),
        ]
        aliases[slug] = [_normalize(value) for value in values if value]
    return aliases


def _score_chunk(
    chunk: Dict[str, Any],
    normalized_query: str,
    query_terms: List[str],
    aliases: Dict[str, List[str]],
) -> int:
    component_slug = str(chunk.get("componentSlug", ""))
    searchable = _normalize(
        " ".join(
            [
                str(chunk.get("title", "")),
                str(chunk.get("component", "")),
                str(chunk.get("subtitleZh", "")),
                str(chunk.get("importName", "")),
                str(chunk.get("text", "")),
            ]
        )
    )

    score = 0
    for alias in aliases.get(component_slug, []):
        if alias and alias in normalized_query:
            score += 12

    for term in query_terms:
        if term in searchable:
            score += 1
            if term in _normalize(str(chunk.get("title", ""))):
                score += 3
            if term == _normalize(str(chunk.get("component", ""))):
                score += 6

    if chunk.get("kind") == "component-docs" and score > 0:
        score += 2
    return score


def _query_terms(query: str) -> List[str]:
    terms = [_normalize(match.group(0)) for match in _WORD_RE.finditer(query)]
    return _unique(term for term in terms if len(term) >= 2)


def _normalize(value: str) -> str:
    return value.strip().lower()


def _unique(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:max_length].rstrip()}\n..."
