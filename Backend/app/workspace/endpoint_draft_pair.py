"""未确认设计草稿同样成对提交，JSON 最后发布并绑定 Markdown 原始字节。"""
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4


def publish_draft_pair(path: Path, value: dict) -> dict:
    """同锁内准备两份文件并刷盘，失败恢复原字节，绝不产生正式确认。"""
    from app.workspace.endpoint_design_documents import _prepare_text_file, _publish_temporary_file, _restore_file, _sync_directory
    payload = dict(value)
    payload["artifactRevision"] = uuid4().hex
    draft = payload["draft"]
    if draft.get("implementationKind") == "agent_runtime":
        from app.services.agent_endpoint_design import render_agent_endpoint_markdown
        markdown = render_agent_endpoint_markdown({**draft, "artifactRevision": payload["artifactRevision"]})
    else:
        from app.workspace.endpoint_design_documents import render_endpoint_design_markdown
        markdown = render_endpoint_design_markdown({**draft, "artifactRevision": payload["artifactRevision"]}).replace("- 状态：已确认", "- 状态：草稿，尚未确认")
    payload["markdownSha256"] = sha256(markdown.encode()).hexdigest()
    markdown_path = path.with_suffix(".md")
    before = {target: target.read_bytes() if target.is_file() else None for target in (path, markdown_path)}
    temporary = []
    try:
        md = _prepare_text_file(markdown_path, markdown)
        temporary.append(md)
        internal = _prepare_text_file(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary.append(internal)
        _publish_temporary_file(md, markdown_path)
        _publish_temporary_file(internal, path)
        _sync_directory(path.parent)
    except Exception:
        for target, content in before.items():
            _restore_file(target, content)
        raise
    finally:
        for target in temporary:
            target.unlink(missing_ok=True)
    return payload
