"""为模拟数据库测试保存指定 SQL，跳过模型调用和远端执行。"""

from hashlib import sha256
from pathlib import Path

from app.domain.entity_tables import EntityDdlRecord
from app.workspace.endpoint_design_documents import technical_plan_path
from app.workspace.entity_ddl_documents import write_confirmed_ddl


def save_fixture_ddl(root: Path, entity: dict, definitions: list[str], *, comment: str = "测试实体") -> str:
    """将用例指定的列与键作为固定已确认 SQL 保存到当前测试工作区。"""

    root = root.resolve()
    plan_path = technical_plan_path(root)
    table_name = entity["table_name"]
    body = ",\n  ".join(definitions)
    sql = (f"CREATE TABLE `{table_name}` (\n  {body}\n) "
           f"ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='{comment}';")
    record = EntityDdlRecord(entity_id=entity["id"], table_name=table_name,
                             plan_digest=sha256(plan_path.read_bytes()).hexdigest(), draft_id="a" * 32, sql=sql)
    return write_confirmed_ddl(root, plan_path, record).sql
