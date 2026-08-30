import unittest

from app.services.build_task_planner import strip_platform_owned_candidate_tasks


class PlatformOwnedTaskFilterTests(unittest.TestCase):
    """验证模型候选不会取得平台资源与路由投影的执行权。"""

    def test_discards_registry_and_rewrites_dependencies(self) -> None:
        """误输出的平台注册任务及其依赖必须在范围校验前移除。"""

        tasks, ignored = strip_platform_owned_candidate_tasks(
            [
                {
                    "id": "page",
                    "unit_id": "page:orders",
                    "dependencies": ["registry", "api"],
                },
                {
                    "id": "registry",
                    "unit_id": "frontend:route-registry",
                    "dependencies": [],
                },
            ]
        )

        self.assertEqual(ignored, ["registry"])
        self.assertEqual(tasks, [{"id": "page", "unit_id": "page:orders", "dependencies": ["api"]}])


if __name__ == "__main__":
    unittest.main()
