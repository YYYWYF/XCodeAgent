"""在同一技术规划节点内生成并缓存片段，最后组装现有四段候选。"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Callable

from app.agents.main.technical_plan_generation_prompts import page_prompt
from app.agents.main.technical_plan_generation_validation import validate_page
from app.agents.main.technical_plan_catalog_repair import CatalogRepairRequired
from app.agents.main.technical_plan_catalog_parts import generate_catalog
from app.agents.main.technical_plan_contract_batches import generate_contract
from app.agents.main.technical_plan_fragment_output import parse_batch_json
from app.services.run_workers import run_worker
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from app.utils.model_output import extract_json_root_object_with_repair


def _fragment(cache: dict, key: str, prompt: str, invoke: Callable[[str], str], validate: Callable[[dict], dict], save: Callable[[str, dict], None]) -> dict:
    """只重试尚未通过校验的片段，完整包络解析失败时不提取内部嵌套对象。"""

    if key not in cache:
        # 每个模型请求前重新检查当前 Run，停止后不得开始下一个模块。
        with run_worker():
            output = invoke(prompt)
        try:
            value = parse_batch_json(output) if key.startswith(("contract-part:", "catalog-part:")) else extract_json_root_object_with_repair(output)
            if not isinstance(value, dict):
                raise ValueError("模型未返回完整 JSON 根对象，无法恢复当前片段。")
            validated = deepcopy(validate(value))
            # 持久化先于内存发布，连接异常、取消或进程退出后仍可读取已完成片段。
            with run_worker():
                save(key, validated)
            cache[key] = validated
        except CatalogRepairRequired:
            raise
        except (TypeError, ValueError) as exc:
            raise ValueError(f"技术规划片段 {key} 校验失败：{exc}") from exc
    return deepcopy(cache[key])


def generate_technical_plan_sections(
    requirement: dict, *, invoke: Callable[[str], str], fragments: dict | None = None,
    max_output_tokens: int = 16384,
) -> dict:
    """统一实体和接口目录后逐模块生成，已成功片段仅在相同上游与原始要求下复用。"""

    frozen = deepcopy(requirement)
    product = frozen["confirmed_product_plan"]
    identity = {
        "product": product,
        "ui": frozen.get("confirmed_ui_design_sha256"),
        "application_config": frozen.get("application_config"),
        "request": frozen.get("planning_adjustment_request", ""),
    }
    fingerprint = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    progress = fragments if fragments is not None else {}
    if progress.get("input_sha256") != fingerprint:
        # 上游、配置或真实修改要求变化时整批失效，不混合不同轮次的业务决策。
        progress.clear()
        progress.update(input_sha256=fingerprint, completed={})
    cache = progress["completed"]
    workspace = frozen.get("technical_plan_generation_workspace")
    store = TechnicalPlanFragmentStore(workspace) if workspace else None
    if store is not None:
        cache.clear()
        cache.update(store.load(fingerprint))

    def publish_fragments(value: dict) -> None:
        """先原子发布当前目录，再更新内存，确保重试和进程重启看到相同依赖版本。"""

        with run_worker():
            if store is not None:
                store.replace(fingerprint, value)
        cache.clear()
        cache.update(value)

    def save_fragment(key: str, value: dict) -> None:
        """生产运行同步保存片段，纯函数调用只使用当前内存候选。"""

        if store is not None:
            store.save(fingerprint, key, value)

    repair = cache.get("catalog_repair")
    def read_fragment(key: str, prompt: str, validate) -> dict:
        """让内部 Contract 批次复用同一持久化、取消检查与原有外层重试预算。"""

        return _fragment(cache, key, prompt, invoke, validate, save_fragment)

    catalog = generate_catalog(frozen, cache=cache, fragment=read_fragment, publish=publish_fragments)
    if repair:
        # 缺口诊断已经由新目录解决，不能再回灌给后续模块使其误判新目录仍缺接口。
        frozen["technical_plan_validation_feedback"] = ""
    contracts = []
    for spec in catalog["contracts"]:
        contracts.append(generate_contract(
            frozen, catalog, spec, cache=cache, fragment=read_fragment, publish=publish_fragments,
            max_output_tokens=max_output_tokens,
        ))
    pages = []
    for page in product.get("pages", []):
        try:
            pages.append(_fragment(
                cache, f"page:{page['pageId']}", page_prompt(frozen, catalog, page, contracts), invoke,
                lambda value, page=page: validate_page(value, page, contracts), save_fragment,
            ))
        except CatalogRepairRequired as exc:
            # 缺口属于目录时撤下旧目录，下一轮预算必须先修目录，不能空转重试页面。
            publish_fragments({**{key: value for key, value in cache.items() if key != "catalog"},
                "catalog_repair": {"catalog": catalog, "baseline": catalog, "issue": exc.issue}})
            raise
    # 架构由现有物化器按权威应用配置和固定技术栈补齐，模型不再重复生成。
    return {"architecture": {}, "entities": deepcopy(catalog["entities"]), "api_contracts": contracts, "pages": pages}
