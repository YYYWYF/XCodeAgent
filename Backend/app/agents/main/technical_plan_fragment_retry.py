"""技术规划片段独立重试、失败收口及依赖修复预算身份。"""

from copy import deepcopy
import logging
from typing import Callable

from app.agents.main.technical_plan_catalog_repair import CatalogRepairRequired
from app.agents.main.technical_plan_contract_batch_validation import SchemaPlanRepairRequired
from app.agents.main.technical_plan_contract_budget import contract_part_prefix
from app.agents.main.technical_plan_fragment_output import PlanningOutputTruncated, parse_batch_json
from app.services.run_workers import run_worker
from app.utils.model_output import extract_json_root_object_with_repair


logger = logging.getLogger("uvicorn.error")
FRAGMENT_ATTEMPTS = 3


class PlanningFragmentFailed(ValueError):
    """片段已收口，外层不得再为同一失败启动完整生成重试。"""

    def __init__(self, key: str, attempts: int, reason: str):
        """保留模块和实际尝试数，沿现有 AG-UI 失败审阅链路展示诊断。"""

        self.fragment_key = key
        self.attempts = attempts
        # 保留既有错误前缀，前端沿原规则去掉内部片段身份后仍可展示尝试数和原因。
        super().__init__(f"技术规划片段 {key} 校验失败：已尝试 {attempts} 次。{reason}。已完成片段已保留，重试将从未完成片段继续。")


def dependency_repair_key(error: ValueError) -> str | None:
    """依赖修复按稳定归属计数，目录内容或批次哈希变化不能重置同一修复链。"""

    if isinstance(error, CatalogRepairRequired):
        return "catalog:" + error.issue["pageId"]
    key = getattr(error, "fragment_key", "")
    if isinstance(error, SchemaPlanRepairRequired):
        return ":".join(key.split(":")[:2]) if key else "schema-plan"
    if isinstance(error, PlanningOutputTruncated):
        # 整个 Contract 转分批和组内二分都保持确定的有限任务树，不重放成功邻居。
        return contract_part_prefix(key[9:]) if key.startswith("contract:") else key or "output"
    return None


def generate_fragment(cache: dict, key: str, prompt: str, invoke: Callable[[str], str], validate: Callable[[dict], dict], save: Callable[[str, dict], None]) -> dict:
    """每个未完成片段独享三次解析校验机会，只有成功持久化后才发布缓存。"""

    if key in cache:
        logger.info("technical_plan_fragment_reused: fragment=%s", key)
        return deepcopy(cache[key])
    feedback = ""
    for attempt in range(1, FRAGMENT_ATTEMPTS + 1):
        # 保留阶段首行和原只读上下文位置，仅在本片段提示中插入最新反馈。
        first, separator, rest = prompt.partition("\n")
        current_prompt = first + separator + (feedback + "\n" if feedback else "") + rest
        logger.info("technical_plan_fragment_attempt: fragment=%s attempt=%s/%s", key, attempt, FRAGMENT_ATTEMPTS)
        # 调用与保存各自检查取消；解析失败也不能绕过下一轮调用前的停止检查。
        with run_worker():
            try:
                output = invoke(current_prompt)
            except PlanningOutputTruncated as exc:
                exc.fragment_key = key
                exc.fragment_attempt = attempt
                raise
            except (TypeError, ValueError) as exc:
                # 模型调用边界错误不是片段格式错误，交给既有基础设施失败路径，不叠加重放。
                raise PlanningFragmentFailed(key, attempt, str(exc)) from exc
        try:
            value = parse_batch_json(output) if key.startswith(("contract-part:", "catalog-part:")) else extract_json_root_object_with_repair(output)
            if not isinstance(value, dict):
                raise ValueError("输出必须是唯一完整的 JSON 根对象，不能包含多个对象、说明文字或未闭合结构")
            validated = deepcopy(validate(value))
        except (CatalogRepairRequired, SchemaPlanRepairRequired) as exc:
            # 不能在无权修改上游的片段内重复生成，保持原依赖失效与修复路径。
            exc.fragment_key = key
            raise
        except (TypeError, ValueError) as exc:
            logger.warning("technical_plan_fragment_retry: fragment=%s attempt=%s/%s error=%s", key, attempt, FRAGMENT_ATTEMPTS, exc)
            if attempt == FRAGMENT_ATTEMPTS:
                raise PlanningFragmentFailed(key, attempt, str(exc)) from exc
            # 每轮只追加最新诊断；基础提示和原始要求不变，其他片段不接收这份反馈。
            feedback = f"\n当前片段第 {attempt} 次输出校验失败：{exc}\n只修正当前片段，严格返回本阶段要求的唯一完整 JSON 根对象，不附加说明文字。"
            continue
        # 存储失败不消耗模型重试机会，也不能把未落盘候选发布成成功缓存。
        with run_worker():
            try:
                save(key, validated)
            except (TypeError, ValueError) as exc:
                raise PlanningFragmentFailed(key, attempt, f"片段保存失败：{exc}") from exc
        cache[key] = validated
        return deepcopy(validated)
