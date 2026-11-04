"""仅投影七个受管权限文件，并以摘要和持久事务阻止覆盖未知修改。"""

import json
import re
from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.authorization_table_projection import sql_tokens, table_mapping
from app.services.template_reconcile.executor_v3 import WorkingCopyStoreV3
from app.services.template_reconcile.state_v3 import load_template_state_v3
from app.services.template_reconcile.transaction_v4 import apply_working_copy_v4
from app.services.template_reconcile.runtime_v2 import reconcile_run_gate
from app.services.authorization_projection_sources import PO_FILES, XML_FILES, SOURCE_RECEIPT, source_digest

RUNTIME = WORKSPACE_ARTIFACT_DIR / "runtime/builtin-authorization-bootstrap"


def read_record(root: Path, name: str) -> dict:
    """只读取工作区内普通状态文件，损坏时拒绝作为首次运行处理。"""

    path = root / RUNTIME / name
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError("权限初始化状态路径不安全。")
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("权限初始化状态损坏，请人工核对。")
    return value


def _project_xml(text: str, mapping: dict[str, str]) -> str:
    """仅在 XML 文本中的 SQL 表引用锚点改写，跳过标签、注释和字符串。"""

    edits = []
    # 保留 XML 原字节排版；标签与注释不能作为 SQL 关键字参与匹配。
    masked = re.sub(r'''<!--[\s\S]*?-->|<!\[CDATA\[|\]\]>|</?[A-Za-z](?:[^>"']|"[^"]*"|'[^']*')*>|<\?[^>]*>''', lambda m: " " * len(m.group()), text)
    tokens = sql_tokens(masked)
    count = 0
    depth, from_clause = 0, False
    for index, (token, _, _) in enumerate(tokens[:-1]):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        if token.upper() == "FROM":
            from_clause = True
        elif token.upper() in {"WHERE", "GROUP", "ORDER", "HAVING", "UNION", "SET", "VALUES", ";"}:
            from_clause = False
        if from_clause and depth == 0 and token == ",":
            raise ValueError("权限 Mapper 不支持逗号多表引用，请显式核对模板 SQL。")
        if token.upper() not in {"FROM", "JOIN", "UPDATE", "INTO"}:
            continue
        name, begin, end = tokens[index + 1]
        logical = name.strip("`")
        if logical not in mapping and logical not in mapping.values():
            raise ValueError("权限 Mapper 包含未知 SQL 表引用。")
        count += 1
        if logical in mapping:
            replacement = mapping[logical]
            edits.append((begin, end, f"`{replacement}`" if name.startswith("`") else replacement))
    if count == 0:
        raise ValueError("权限 Mapper 缺少 SQL 表引用锚点。")
    for begin, end, replacement in reversed(edits):
        text = text[:begin] + replacement + text[end:]
    return text


def ensure_projection(root: Path, key: str) -> None:
    """与模板安装共用写锁，在模板提交后原子检查及恢复本地投影。"""

    with reconcile_run_gate(root):
        _ensure_projection(root, key)


def _ensure_projection(root: Path, key: str) -> None:
    """验证全部文件后一次提交；相同模板覆盖可恢复，未知编辑和版本变化拒绝覆盖。"""

    mapping = table_mapping(key)
    template = load_template_state_v3(root)
    owners = {artifact.target: artifact for artifact in template.installedArtifacts.values()}
    previous = read_record(root, "projection.json")
    if previous and previous.get("datasourceKey") != key:
        raise ValueError("权限投影绑定已变化，请核对受管文件后处理。")
    store, proofs = WorkingCopyStoreV3(root), {}
    source_receipt = store.entry(SOURCE_RECEIPT.as_posix()).original
    if source_receipt is None:
        raise ValueError("缺少模板安装时记录的权限来源摘要，不能信任现有文件并覆盖。")
    sources = json.loads(source_receipt).get("files", {})
    for relative in (*PO_FILES, *XML_FILES):
        owner = owners.get(relative)
        if owner is None or owner.capabilityId != "authorization":
            raise ValueError("权限投影文件缺少模板受管来源。")
        entry = store.entry(relative)
        if entry.original is None or len(entry.original) > 256 * 1024:
            raise ValueError("权限投影文件缺失或超过大小上限。")
        source = sources.get(relative)
        proof = previous.get("files", {}).get(relative)
        actual = source_digest(entry.original)
        trusted_source = isinstance(source, dict) and source.get("installedRevision") == owner.installedRevision and actual == source.get("digest")
        trusted_projection = (isinstance(proof, dict) and isinstance(source, dict)
                              and proof.get("source") == source.get("digest") and proof.get("projected") == actual
                              and source.get("installedRevision") == owner.installedRevision)
        text = entry.original.decode("utf-8")
        if relative in PO_FILES:
            logical = PO_FILES[relative]
            matches = list(re.finditer(r'@TableName\(\s*"([^"\n]+)"\s*\)', text))
            if len(matches) != 1 or matches[0][1] not in {logical, mapping[logical]}:
                raise ValueError("权限 PO 的 TableName 锚点冲突。")
            match = matches[0]
            updated = text[:match.start(1)] + mapping[logical] + text[match.end(1):]
            canonical = text[:match.start(1)] + logical + text[match.end(1):]
        else:
            updated = _project_xml(text, mapping)
            canonical = _project_xml(text, {physical: logical for logical, physical in mapping.items()})
        # 新分支可能带着已提交的投影文件但没有 runtime；反向还原摘要必须精确等于安装来源。
        trusted_roundtrip = (isinstance(source, dict) and source.get("installedRevision") == owner.installedRevision
                             and source_digest(canonical.encode("utf-8")) == source.get("digest"))
        if not trusted_source and not trusted_projection and not trusted_roundtrip:
            raise ValueError("权限文件存在未知修改或模板来源变化，请人工解决冲突。")
        proofs[relative] = {"source": source["digest"], "projected": source_digest(updated.encode("utf-8"))}
        entry.working = updated.encode("utf-8")
    # 投影收据与七个文件处于同一持久事务，写入失败由已有事务设施恢复。
    receipt = store.entry((RUNTIME / "projection.json").as_posix())
    receipt.working = json.dumps({"datasourceKey": key, "templateRevision": template.templateRevision,
                                "files": proofs}, ensure_ascii=False, sort_keys=True).encode()
    apply_working_copy_v4(root, store)
