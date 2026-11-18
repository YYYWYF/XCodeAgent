"""验证原任务协议、完整职责反馈及共享文件创建顺序，不引入复用引用。"""

from copy import deepcopy
import json
import unittest

from app.agents.main.unit_task_prompt import build_unit_generation_prompt
from app.services.backend_module_scope import module_task_dependencies, scoped_task_ids
from app.services.unit_candidate_parser import RawUnitCandidateParseError, parse_raw_unit_candidate
from app.services.unit_candidate_validator import validate_unit_candidate
from app.services.unit_generation_contracts import UnitGenerationContext
from tests.test_unit_generation_contracts import _context_payload


ROOT = "backend/src/main/java/com/example/todo"
DTO = ROOT + "/application/dto/TodoOutput.java"
SERVICE = ROOT + "/application/service/TodoApplicationService.java"


def context_payload() -> dict:
    """提供三个接口的当前模块职责及空工程快照。"""

    payload = _context_payload()
    requirements = []
    for endpoint in ("todo_api.create", "todo_api.detail", "todo_api.mark_complete"):
        for kind in ("backend.application_service", "backend.objects", "backend.endpoint_controller"):
            prefix = "backend.endpoint.objects" if kind == "backend.objects" else kind
            identity = f"{prefix}:todo_api:{endpoint}"
            requirements.append({
                "requirement_id": identity, "description": "落实正式接口职责",
                "source_refs": {"kind": kind, "capability_id": identity, "artifact": "endpoint-api-design",
                                "api_contract_id": "todo_api", "endpoint_id": endpoint,
                                "target_id": endpoint, "data_source_types": []},
            })
    payload.update(unit_id="backend:module:todo_api", unit_kind="backend", generation_requirements=requirements,
                   build_execution_scope={"type": "page", "targetId": "page_todo_list"},
                   workspace_context={"backend": {"dir_structure": "backend/"}},
                   dependency_context={"dependency_unit_ids": [], "retained_task_summaries": []},
                   constraints={"owner": "backend", "managed_files": []})
    return payload


def file_plan(path: str, covers: list[int], description: str) -> dict:
    """构造原协议的文件计划，不附带额外任务字段。"""

    return {"path": path, "operation": "add", "description": description, "covers": covers}


def draft() -> dict:
    """按原文件清单表达每个接口的 Service 和对象职责。"""

    tasks = []
    for endpoint in ("create", "detail", "mark_complete"):
        tasks.append({"key": "business:todo_api." + endpoint, "title": endpoint, "files": [
            file_plan(DTO, [2], "落实当前接口响应对象字段与类型化映射，保留兼容定义。"),
            file_plan(SERVICE, [1], f"实现 {endpoint} 方法并保留已有业务方法。"),
        ]})
    tasks.append({"key": "controller", "title": "待办控制器", "files": [
        file_plan(ROOT + "/adapter/web/TodoController.java", [1, 2, 3], "绑定内部路由并委托 Service。"),
    ]})
    return {"tasks": tasks}


def parse(value: dict) -> list[dict]:
    """经真实 Parser 和确定性任务绑定处理候选。"""

    context = UnitGenerationContext.model_validate(context_payload())
    return parse_raw_unit_candidate(json.dumps(value, ensure_ascii=False), unit_id=context.unit_id,
                                    task_identity_context=context)


class BackendModuleFeedbackTests(unittest.TestCase):
    """覆盖收缩后的协议边界和本轮自检发现的风险入口。"""

    def test_valid_original_protocol_remains_unchanged(self) -> None:
        """原协议能够生成合格候选，交付物仍在自身文件范围内。"""

        source = draft()
        original = deepcopy(source)
        tasks = parse(source)
        context = UnitGenerationContext.model_validate(context_payload())
        self.assertEqual(validate_unit_candidate(context, tasks), [])
        self.assertEqual(source, original)
        for task in tasks:
            self.assertEqual(task["dependencies"], [])
            self.assertNotIn("source_refs", task)
            self.assertTrue(all(set(d["paths"]) <= set(task["target_files"]) for d in task["deliverables"]))

    def test_missing_responsibilities_are_returned_for_all_tasks(self) -> None:
        """两个接口同时漏对象职责时一次返回完整身份，不只报告第一处。"""

        value = draft()
        for task in value["tasks"][1:3]:
            task["files"] = [task["files"][1]]
        with self.assertRaises(RawUnitCandidateParseError) as result:
            parse(value)
        self.assertEqual([issue.details["task_key"] for issue in result.exception.issues],
                         ["business:todo_api.detail", "business:todo_api.mark_complete"])
        for issue in result.exception.issues:
            self.assertTrue(issue.task_ids)
            self.assertEqual(issue.details["missing_responsibilities"][0]["kind"], "backend.objects")

    def test_wrong_object_and_forward_reference_fields_are_rejected(self) -> None:
        """错对象复用和改变任务顺序的引用均不能再进入候选或放宽对象范围。"""

        for provider, path in (("business:todo_api.create", ROOT + "/application/dto/TodoCreateInput.java"),
                               ("business:todo_api.mark_complete", DTO)):
            with self.subTest(provider=provider, path=path):
                value = draft()
                value["tasks"][1]["reuses"] = [{"task": provider, "path": path, "covers": [2]}]
                with self.assertRaises(RawUnitCandidateParseError) as result:
                    parse(value)
                self.assertIn("只能包含 key、title、files", str(result.exception))

    def test_shared_service_keeps_creator_when_model_output_is_reordered(self) -> None:
        """模型数组逆序不会改变平台原清单顺序，共享 Service 仍有创建任务。"""

        value = draft()
        value["tasks"] = list(reversed(value["tasks"]))
        tasks = parse(value)
        operations = [(task["id"], change["operation"]) for task in tasks for change in task["change_scope"]
                      if change["path"] == SERVICE]
        self.assertTrue(operations[0][0].endswith("todo_api.create::business"))
        self.assertEqual([operation for _, operation in operations], ["add", "modify", "modify"])

    def test_endpoint_slice_does_not_pull_another_business_task(self) -> None:
        """单接口任务切片不再因新增的对象引用带入其他接口业务任务。"""

        tasks = parse(draft())
        for task in tasks:
            task["source_refs"] = {"backend_stage": "controller" if task["id"].endswith("::controller") else "business"}
        dependencies = module_task_dependencies(tasks, {}, {("todo_api", "todo_api.detail")})
        for task in tasks:
            task["dependencies"] = dependencies[task["id"]]
        selected = scoped_task_ids(tasks, {"type": "endpoint", "apiContractId": "todo_api", "targetId": "todo_api.detail"},
                                   {"backend:module:todo_api"})
        selected_business = [task["id"] for task in tasks if task["id"] in selected and task["source_refs"]["backend_stage"] == "business"]
        self.assertEqual(len(selected_business), 1)
        self.assertTrue(selected_business[0].endswith("todo_api.detail::business"))

    def test_wrong_covers_are_reported_without_automatic_reassignment(self) -> None:
        """对象标错职责仍然失败，不靠自动补号伪装成完整交付。"""

        value = draft()
        value["tasks"][0]["files"][0]["covers"] = [1]
        with self.assertRaises(RawUnitCandidateParseError) as result:
            parse(value)
        self.assertEqual(result.exception.issues[0].details["task_key"], "business:todo_api.create")

    def test_mixed_task_errors_are_aggregated_without_partial_result(self) -> None:
        """文件错误和另一接口漏项一并反馈，不返回部分可执行任务。"""

        value = draft()
        value["tasks"][0]["files"][0]["path"] = "../unsafe.java"
        value["tasks"][1]["files"] = [value["tasks"][1]["files"][1]]
        with self.assertRaises(RawUnitCandidateParseError) as result:
            parse(value)
        self.assertEqual(len(result.exception.issues), 2)

    def test_prompt_keeps_meanings_thinking_and_original_output_shape(self) -> None:
        """只增强职责说明，保留思考及原 JSON 字段，不再引入复用协议。"""

        prompt = build_unit_generation_prompt(UnitGenerationContext.model_validate(context_payload()))
        for text in ("DTO files must cover the objects index", "exactly `key`, `title`, and `files`",
                     "covering every listed index", "任务规划保留充分思考能力"):
            self.assertIn(text, prompt)
        self.assertNotIn("reuses", prompt)
