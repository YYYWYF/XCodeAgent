"""复用 DeepAgents 文件工具，在隔离的内存候选上局部修订技术规划。"""

from __future__ import annotations

from copy import deepcopy
import json
from threading import Lock
from typing import Any, Callable

from deepagents.backends.protocol import EditResult, GrepResult, ReadResult, WriteResult
from deepagents.backends.state import StateBackend
from deepagents.backends.utils import create_file_data, file_data_to_string
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.errors import GraphRecursionError

from app.agents.execution_control import RunCancellationMiddleware
from app.agents.main.technical_plan_feedback import technical_plan_feedback_prompt
from app.agents.model_factory import create_chat_model
from app.config import Settings


TECHNICAL_PLAN_CANDIDATE_PATH = "/technical-plan.json"
PRODUCT_CONTEXT_PATH = "/context/product-plan.json"
CONSTRAINTS_CONTEXT_PATH = "/context/constraints.json"
TECHNICAL_PLAN_EDIT_SECTIONS = ("architecture", "entities", "api_contracts", "pages")
# Agent 合同是可选第五段：只在既有候选已包含时原样带入编辑器，避免二次修改丢失七模块定义。
TECHNICAL_PLAN_EDIT_OPTIONAL_SECTIONS = ("agent_contracts",)
TECHNICAL_PLAN_EDIT_MODEL_LIMIT = 8


class TechnicalPlanCandidateError(ValueError):
    """携带已解析但尚未通过平台物化的候选，供下一轮继续局部修复。"""

    def __init__(self, message: str, candidate: dict[str, Any]) -> None:
        """复制失败候选，避免异常处理期间回写模型输出或正式基线。"""

        super().__init__(message)
        self.candidate = deepcopy(candidate)


def has_editable_technical_plan_candidate(value: Any) -> bool:
    """只接纳四段结构完整的候选，语义错误仍交给编辑器和全量校验处理。"""

    return (
        isinstance(value, dict)
        and isinstance(value.get("architecture"), dict)
        and all(
            isinstance(value.get(key), list)
            and all(isinstance(item, dict) for item in value[key])
            for key in ("entities", "api_contracts", "pages")
        )
    )


class TechnicalPlanCandidateBackend(StateBackend):
    """使用现成内存文件后端，只允许精确编辑本轮技术规划候选。"""

    def __init__(self) -> None:
        """为每次独立修订记录真正应用成功的局部修改次数。"""

        super().__init__()
        self.successful_edits = 0
        self.last_edit_error: str | None = None
        self._edit_lock = Lock()
        self._latest_candidate: dict | None = None

    def _read_files(self) -> dict[str, Any]:
        """向同批工具提供最新候选，避免各工具任务仍读取批次开始时的状态快照。"""

        files = dict(super()._read_files())
        if self._latest_candidate is not None:
            files[TECHNICAL_PLAN_CANDIDATE_PATH] = self._latest_candidate
        return files

    def _send_files_update(self, update: dict[str, Any]) -> None:
        """沿用原生状态写入，并在本次隔离运行内同步最新候选。"""

        super()._send_files_update(update)
        if TECHNICAL_PLAN_CANDIDATE_PATH in update:
            self._latest_candidate = update[TECHNICAL_PLAN_CANDIDATE_PATH]

    def candidate_text(self) -> str:
        """读取最终候选，避免 Graph 按工具声明顺序合并快照时覆盖较晚完成的修改。"""

        if self._latest_candidate is None:
            raise ValueError("技术规划候选尚未经过局部编辑。")
        return file_data_to_string(self._latest_candidate)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """按有界窗口读取候选或只读上下文，避免一次回灌完整大规划。"""

        return super().read(file_path, offset=offset, limit=min(limit, 120))

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None,
        *, max_count: int | None = None,
    ) -> GrepResult:
        """复用原生搜索并限制匹配数量，模型可缩小搜索范围继续定位。"""

        return super().grep(pattern, path, glob, max_count=min(max_count or 40, 40))

    def edit(
        self, file_path: str, old_string: str, new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """串行应用同一候选的编辑，关联片段始终基于上一项已成功的修改。"""

        with self._edit_lock:
            return self._edit_candidate(file_path, old_string, new_string, replace_all)

    def _edit_candidate(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool,
    ) -> EditResult:
        """仅允许对候选执行唯一文本匹配，拒绝写上游或批量误替换。"""

        error = None
        if file_path != TECHNICAL_PLAN_CANDIDATE_PATH:
            error = "只能编辑 /technical-plan.json；上游上下文只读。"
        elif replace_all:
            error = "请提供能唯一匹配的上下文，不能使用 replace_all。"
        elif not old_string:
            error = "old_string 不能为空，请先读取需要修改的原文。"
        else:
            current = self._read_files().get(file_path)
            if current and old_string.strip() == file_data_to_string(current).strip():
                error = "不能替换整份候选，请只提交需要修改的局部片段。"
        result = EditResult(error=error) if error else super().edit(file_path, old_string, new_string, replace_all=False)
        if result.error and not error:
            # 精确匹配仍由 DeepAgents 执行；只在失败反馈中提示可唯一定位的短片段，不自动模糊替换。
            compact = old_string.strip()
            source = file_data_to_string(self._read_files()[file_path])
            if compact and compact != old_string and source.count(compact) == 1:
                result = EditResult(error=result.error + "\n去掉展示缩进后，下列原文片段可唯一匹配，请用它重新提交："
                                    + json.dumps(compact, ensure_ascii=False))
            else:
                result = EditResult(error=result.error + "\n请核对 read_file 行号后的分隔空格；"
                                    "优先使用唯一的短键值片段，仍有多处匹配时再加入原文上下文。")
        self.last_edit_error = result.error
        if result.error is None and old_string != new_string:
            self.successful_edits += 1
        return result

    def write(self, file_path: str, content: str) -> WriteResult:
        """拒绝整文件写入及上下文驱逐写入，所有候选修改必须通过 edit_file。"""

        return WriteResult(error="局部技术规划修订只支持 edit_file，不支持整文件写入。")


class _PlanningTokenCallback(BaseCallbackHandler):
    """沿用既有规划 token 通道，不向外发布候选文件或工具参数。"""

    def __init__(self, on_token: Callable[[str], None]) -> None:
        """保存现有规划节点提供的文本回调。"""

        self.on_token = on_token

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """仅转发模型正文，工具参数仍保留在内部工具循环。"""

        if isinstance(token, str) and token:
            self.on_token(token)


def _candidate_files(requirement_spec: dict[str, Any], existing_plan: dict[str, Any]) -> dict:
    """生成必选四段加已存在 Agent 合同的候选和只读上下文，不把应用连接配置或凭据交给模型。"""

    candidate = {key: deepcopy(existing_plan.get(key)) for key in TECHNICAL_PLAN_EDIT_SECTIONS}
    # 已确认的 Agent 七模块定义属于同一份 TechnicalPlan，必须随候选一起进入编辑器并保持原样。
    for key in TECHNICAL_PLAN_EDIT_OPTIONAL_SECTIONS:
        if key in existing_plan:
            candidate[key] = deepcopy(existing_plan[key])
    config = requirement_spec.get("application_config") or {}
    constraints = {
        "backend_stack": "Java8 + Springboot; MySQL8 + Redis",
        "auth_enabled": bool((config.get("auth") or {}).get("enable")),
        "authorization_enabled": bool((config.get("authorization") or {}).get("enabled")),
    }
    documents = {
        TECHNICAL_PLAN_CANDIDATE_PATH: candidate,
        PRODUCT_CONTEXT_PATH: requirement_spec["confirmed_product_plan"],
        CONSTRAINTS_CONTEXT_PATH: constraints,
    }
    return {
        path: create_file_data(json.dumps(value, ensure_ascii=False, indent=2))
        for path, value in documents.items()
    }


def _editor_system_prompt(requirement_spec: dict[str, Any]) -> str:
    """提供局部编辑规则和持续有效的原始修改要求，不注入完整规划正文。"""

    return (
        "你负责修订当前 TechnicalPlan。只使用 grep、read_file、edit_file 工具在内存候选上"
        "修改相关片段，保留其他全部内容。不要在最终回答中输出整份规划、Contract 或 JSON。\n"
        f"可编辑文件：{TECHNICAL_PLAN_CANDIDATE_PATH}。"
        f"只读的已确认产品事实：{PRODUCT_CONTEXT_PATH}；应用约束：{CONSTRAINTS_CONTEXT_PATH}。\n"
        "先 grep 定位名称、ID、路径或字段，再 read_file 读取相关窗口（offset 从0开始，每次最多120行）。"
        "grep 的 content 模式可以返回匹配行号，read_file 的行号前缀不属于原文，不能放入 old_string。"
        "edit_file 的 old_string 必须精确且唯一匹配；若匹配不到或存在多处，补读上下文后再修改。"
        "只改唯一的键值或文字时，直接用不带前导缩进的最短唯一片段；例如将"
        "old_string 设为 '\"name\": \"旧名称\"'，new_string 设为 '\"name\": \"新名称\"'。"
        "read_file 在行号后另加两个展示分隔空格，不属于原文缩进。不要重复提交已经失败的匹配片段。"
        "允许在同一轮使用多次 edit_file 完成关联修改，也允许中间文件暂时不是有效 JSON；"
        "结束前必须读回改动并确保整个候选恢复为有效 JSON。不要为解决匹配失败重写整个文件。\n"
        "候选根对象必须包含 architecture、entities、api_contracts、pages；"
        "若候选已含 agent_contracts（已确认的业务 Agent 七模块定义），必须整段保留，"
        "只在相关页面或接口变动时同步修改其中的 agentId、pageIds 和 tools 绑定，不能删除该段，"
        "也不能在没有该段时自行新增。"
        "架构仅有 frontend/backend/data；所有用户可见说明使用简体中文。"
        "保持未涉及的稳定ID、接口、Schema、参数、页面绑定和字段原样。"
        "修改字段或接口后，搜索相关 entity_field_ref、$ref、endpointId、endpoint_id 并补齐必要引用；"
        "Schema 引用必须在所属 Contract 内闭合。分页参数使用 current/pageSize，分页响应同级字段"
        "只能为 total/pageSize/current/list。Contract 和 Endpoint 必须有非空中文 name。"
        "不要生成 authorization_manifest、资源键、角色授权或应用配置；这些由平台编译。"
        "已确认 ProductPlan 和应用配置不能在此修改。遇到角色、权限业务规则或产品范围变更，"
        "说明需要上游修订，不能伪造技术字段绕过。"
        "最终仅简短说明已修改内容或未完成原因；文件才是候选来源。外层平台会执行全量校验，"
        "系统校验错误只用于继续局部修复，不能撤销原始意见。生成后仍须正式确认。\n"
        + technical_plan_feedback_prompt(requirement_spec)
    )


def edit_technical_plan_with_agent(
    requirement_spec: dict[str, Any], existing_plan: dict[str, Any], *,
    settings: Settings, on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """运行受控的原生工具循环，读取内存候选，始终不写工作区正式文件。"""

    model = create_chat_model(settings, extra_model_kwargs={"thinking": {"type": "disabled"}})
    backend = TechnicalPlanCandidateBackend()
    agent = create_agent(
        name="technical-plan-fragment-editor",
        model=model,
        system_prompt=_editor_system_prompt(requirement_spec),
        middleware=[
            RunCancellationMiddleware(model),
            FilesystemMiddleware(
                backend=backend,
                tools=["grep", "read_file", "edit_file"],
                custom_tool_descriptions={
                    "edit_file": "精确替换内存候选中的唯一片段。先读取相关原文；优先提交最短唯一键值或文字，"
                    "不要加入 read_file 的行号及其后两个展示分隔空格。old_string 不匹配时按错误反馈修正后再试，"
                    "多处匹配时扩大原文上下文。只允许 /technical-plan.json，replace_all 必须为 false。",
                },
                tool_token_limit_before_evict=None,
                human_message_token_limit_before_evict=None,
            ),
            ModelCallLimitMiddleware(run_limit=TECHNICAL_PLAN_EDIT_MODEL_LIMIT, exit_behavior="error"),
        ],
    )
    callbacks = [_PlanningTokenCallback(on_token)] if on_token else []
    try:
        agent.invoke(
            {"messages": [{"role": "user", "content": "请按原始修改要求和本轮校验反馈局部编辑候选，然后读回复核。"}],
             "files": _candidate_files(requirement_spec, existing_plan)},
            config={"recursion_limit": 24, "callbacks": callbacks},
        )
    except (ModelCallLimitExceededError, GraphRecursionError) as exc:
        raise ValueError("技术规划局部修订达到调用上限，未提交正式产物。") from exc
    if backend.last_edit_error:
        raise ValueError("技术规划局部修改尚有未解决的工具错误：" + backend.last_edit_error)
    if not backend.successful_edits:
        raise ValueError("技术规划模型没有应用任何局部修改；不能用回复文字冒充已修改候选。")
    candidate = json.loads(backend.candidate_text())
    # 期望根字段由既有候选决定：四段必选，Agent 合同随原候选一起保留，不允许模型增删该段。
    expected_sections = set(TECHNICAL_PLAN_EDIT_SECTIONS) | {
        key for key in TECHNICAL_PLAN_EDIT_OPTIONAL_SECTIONS if key in existing_plan
    }
    if not isinstance(candidate, dict) or set(candidate) != expected_sections:
        required = "、".join([
            *TECHNICAL_PLAN_EDIT_SECTIONS,
            *(key for key in TECHNICAL_PLAN_EDIT_OPTIONAL_SECTIONS if key in existing_plan),
        ])
        raise ValueError(f"局部修订候选必须且只能包含 {required}。")
    return candidate
