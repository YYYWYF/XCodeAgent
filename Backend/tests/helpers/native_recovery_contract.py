"""Native Recovery 通用 Fork-Stability 测试辅助函数。"""

from __future__ import annotations

from typing import Any


async def assert_native_recovery_fork_stable(
    *,
    testcase: Any,
    graph: Any,
    checkpoint_config: dict[str, Any],
    expected_next_nodes: list[str],
    runtime_identity_update: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    """使用与生产一致的 identity-only aupdate_state 断言 successor 集合不变。"""

    source = await graph.aget_state(checkpoint_config)
    testcase.assertEqual(list(source.next), list(expected_next_nodes))

    fork_config = await graph.aupdate_state(
        checkpoint_config,
        runtime_identity_update,
    )
    fork = await graph.aget_state(fork_config)
    testcase.assertEqual(list(fork.next), list(expected_next_nodes))
    return fork_config, fork

