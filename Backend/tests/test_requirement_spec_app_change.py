"""需求规格跨迭代合并的应用级变更标记回归。

页面级 `_change_status` 回答不了"应用整体表述是否变了"：设计阶段的"页面风格"
维度取自需求全文（应用摘要、各页描述），没有页面归属，只能靠应用级结论打标。
本模块确保该标记：
1. 首轮创建为 added，基线未变时为 unchanged，摘要变化时为 modified；
2. 只作为展示视图存在，写正式产物前必须被剥离。
"""

import unittest
from copy import deepcopy

from app.services.requirement_spec_merger import (
    APP_CHANGE_STATUS_FIELD,
    merge_requirement_spec,
    strip_change_status,
)


def _spec(summary: str = "应用摘要") -> dict:
    """构造只含应用级内容与一个页面的最小需求规格。"""

    return {
        "app_info": {"name": "库存", "summary": summary},
        "summary": summary,
        "pages": [{"pageId": "home", "name": "首页"}],
    }


class AppLevelChangeStatusTests(unittest.TestCase):
    """应用级标记必须反映真实的需求表述变化。"""

    def test_first_round_is_added(self) -> None:
        """基线为空（首轮创建）时，应用级内容全部是新增。"""

        merged = merge_requirement_spec(None, _spec())

        self.assertEqual(merged[APP_CHANGE_STATUS_FIELD], "added")

    def test_identical_content_is_unchanged(self) -> None:
        """应用摘要与名称都没变时不得誤标为修改。"""

        merged = merge_requirement_spec(_spec(), deepcopy(_spec()))

        self.assertEqual(merged[APP_CHANGE_STATUS_FIELD], "unchanged")

    def test_summary_change_is_modified(self) -> None:
        """应用摘要变化（例如删除某一功能）应判为修改。"""

        merged = merge_requirement_spec(_spec("旧摘要"), _spec("新摘要"))

        self.assertEqual(merged[APP_CHANGE_STATUS_FIELD], "modified")

    def test_marker_is_stripped_before_persisting(self) -> None:
        """标记只是展示视图，不能进入正式产物。"""

        merged = merge_requirement_spec(_spec("旧摘要"), _spec("新摘要"))
        clean = strip_change_status(merged)

        self.assertNotIn(APP_CHANGE_STATUS_FIELD, clean)
        # 页面标记同样被剥离。
        self.assertNotIn("_change_status", str(clean.get("pages")))


if __name__ == "__main__":
    unittest.main()
