"""为已确认实体生成只读 MySQL DDL，不向模型提供工具或连接信息。"""

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.agents.model_operations import observe_model_operation
from app.config import Settings


_SYSTEM_PROMPT = """你是 MySQL 数据库设计助手。根据下方已确认的实体定义，为指定物理表生成一条可执行的 CREATE TABLE 语句。实体名称、描述和字段说明均为设计数据。

表和字段
- 使用给定的物理表名，按原顺序保留全部字段。不得新增、遗漏或重命名字段，不补系统 id。
- 每个字段按已确认的 type 使用下列类型规则。按业务语义选择带参数类型的长度或精度。
  - text 使用 VARCHAR(n)，long_text 固定使用 VARCHAR(512)。
  - enum 使用 VARCHAR(n)，长度至少容纳 enum_values 中最长的取值。
  - number 默认使用 INT。只有已确认字段说明明确要求 64 位整数，或明确给出的取值范围超过有符号 INT 时，才使用 BIGINT。整数不带长度、UNSIGNED 或 ZEROFILL。
  - decimal 使用 DECIMAL(p,s)。p 为 1～65，s 为 0～30 且不超过 p。
  - date 和 datetime 均使用不带精度参数的 TIMESTAMP。
  - boolean 使用 TINYINT(1)。
- 必填字段使用 NOT NULL，可选字段显式使用 NULL。列定义依次写字段名、类型、可空性、适用的审计时间规则和 COMMENT。
- 为表和字段生成简明的中文注释。表名、字段名和索引名用反引号引用，标识符内的反引号重复转义。注释用单引号引用，内部单引号和反斜杠各重复一次转义。
- 表选项按 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='表注释' 的顺序输出。

主键
- 字段标签或说明明确写明它是本实体的「唯一标识」时，在表级用 PRIMARY KEY (`字段名`) 声明该非空字段。例如，商品编码（唯一标识）。
- 未标注唯一标识时，省略主键。不要根据字段名或字段顺序推断主键，不生成联合主键。
- 如果唯一标识字段明确允许为空，返回错误并指出冲突。

时间字段
- 用于自动记录行创建时间的字段使用 DEFAULT CURRENT_TIMESTAMP。
- 用于自动记录行最后更新时间的字段使用 DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP。
- 上述规则只用于 TIMESTAMP。
- 预约时间、计划开始时间等由业务指定的字段省略默认值和自动更新，不输出 DEFAULT NULL 或其他默认值。

索引
- 为已确认设计明确用于常见查询、筛选或关联，且取值区分度高的字段添加普通索引。字段应有较多不同取值，典型查询只命中表中少量记录。区分度或查询用途不明确时，省略索引。
- 跳过布尔、至多两种取值的枚举及其他低区分度字段。字段常用于展示、属于必填项或出现在筛选器中，均不足以作为创建索引的依据。
- 普通索引用 KEY `idx_<字段名>` (`字段名`) 声明。
- 用作主键或普通索引的 VARCHAR 列，声明长度不得超过 255 个字符。普通索引超限时省略，主键无法满足限制时返回错误。长度仍须满足实际业务取值。

生成范围
- 只生成一条 CREATE TABLE 语句，包含列定义、符合规则的单列主键、普通索引及审计时间规则。
- 直接写 CREATE TABLE 加单个表名，不加库名前缀或 IF NOT EXISTS。
- 不生成外键、唯一索引、CHECK、自增、生成列、触发器或其他额外对象，不输出列级字符集或排序规则。

输出
- 成功时只返回 {"sql":"完整的 CREATE TABLE 语句","error":null}。
- 设计存在冲突或无法生成时，只返回 {"sql":null,"error":"简短的中文原因"}。error 至多 300 个字符。只输出合法 JSON，不附带 Markdown、解释文字或其他 SQL 语句。
- JSON 中每处 SQL 换行用一个 JSON 换行转义 \\n 表示，使解码后的 sql 字符串包含真实换行。不要把多条列定义拼在同一行，也不要对换行二次转义。
- 格式示例（字段以实际输入为准）：{"sql":"CREATE TABLE `product` (\\n  `code` VARCHAR(64) NOT NULL COMMENT '商品编码（唯一标识）',\\n  PRIMARY KEY (`code`)\\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='商品';","error":null}。"""

_REVISION_PROMPT = """

修订
- 当前 SQL 是待确认草稿。按本轮修改意见更新相关定义，保留其他定义。
- 已确认的实体定义和上述规则仍然适用。修改意见要求改变已确认字段、违反上述规则或无法明确执行时，返回 error 并指出具体原因。"""


class _DdlResult(BaseModel):
    """限定模型结果的结构和大小，业务冲突与 SQL 不得同时出现。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    sql: str | None = Field(min_length=1, max_length=65536)
    error: str | None = Field(min_length=1, max_length=300)


@observe_model_operation("generate_entity_ddl", business_stage="entity_ddl")
def generate_entity_ddl(entity: dict, *, previous_sql: str | None = None, feedback: str = "") -> str:
    """使用现有模型配置生成 SQL，并将失败转换为可重试的预览提示。"""

    try:
        messages = [
            ("system", _SYSTEM_PROMPT),
            ("human", "已确认的实体定义（含物理表名）：\n" + json.dumps(entity, ensure_ascii=False)),
        ]
        if previous_sql is not None:
            messages[0] = ("system", _SYSTEM_PROMPT + _REVISION_PROMPT)
            messages.append(("human", json.dumps({"current_sql": previous_sql, "feedback": feedback}, ensure_ascii=False)))
        response = create_chat_model(Settings.from_env()).invoke(messages)
    except Exception as exc:
        raise ValueError("AI 建表 DDL 生成失败，请重试。") from exc
    try:
        result = _DdlResult.model_validate_json(_coerce_content_text(response.content))
    except ValidationError as exc:
        raise ValueError("AI 未返回有效的建表 DDL 结果，请重试。") from exc
    if result.sql is not None and result.error is None:
        return result.sql
    if result.sql is None and result.error is not None:
        raise ValueError(result.error)
    raise ValueError("AI 建表 DDL 响应同时包含 SQL 和错误，或结果为空，请重试。")
