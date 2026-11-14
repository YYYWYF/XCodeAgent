"""用指定生成工程的解释器运行会话边界验证；不修改目标、不启动服务、不调用模型。"""

import argparse
import asyncio
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def main() -> None:
    """显式绑定当前生成源码，避免用平台或外部模板夹具冒充生成服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', required=True, type=Path)
    root = parser.parse_args().runtime_root.resolve()
    sys.path.insert(0, str(root / 'src'))
    from app.security.service import resolve_public_principal
    from app.security.tokens import issue_anonymous_session, verify_anonymous_session, SecurityTokenError
    from app.interaction.schemas import parse_run_request, RuntimeRequestError
    from app.interaction.service import _agent_input

    class SessionLifecycleTests(unittest.TestCase):
        """直接消费目标安全实现，使用测试密钥和受控时钟，不读取真实 Cookie。"""

        def setUp(self) -> None:
            """建立隔离设置，不读取 dotenv、模型或现有服务的凭据。"""
            self.settings = SimpleNamespace(namespace='verification-only', anonymous_session_ttl_seconds=10,
                auth_enabled=False, auth_mode='anonymous-session', runtime_profile='preview',
                require_anonymous_session_secret=lambda: 'verification-only-secret-at-least-32-characters')
            with patch('app.security.tokens.time.time', return_value=100):
                self.token = issue_anonymous_session(self.settings, user_id='original-user')

        def resolve(self, at: int, token: str) -> object:
            """经目标真实 Principal 边界恢复匿名身份，不发网络或业务请求。"""
            with patch('app.security.tokens.time.time', return_value=at):
                return asyncio.run(resolve_public_principal(self.settings, request=None,
                    authentication_adapter=None, authorization='', anonymous_token=token))

        def test_valid_session_preserves_identity(self) -> None:
            """到期前保持原身份，不重复签发 Cookie。"""
            current = self.resolve(109, self.token)
            self.assertEqual(current.context.user_id, 'original-user')
            self.assertIsNone(current.anonymous_token)

        def test_exact_expiry_rotates_identity(self) -> None:
            """精确到期边界拒绝旧签名并签发新身份，不恢复旧会话。"""
            with patch('app.security.tokens.time.time', return_value=110):
                with self.assertRaises(SecurityTokenError):
                    verify_anonymous_session(self.settings, self.token)
            current = self.resolve(110, self.token)
            self.assertNotEqual(current.context.user_id, 'original-user')
            self.assertIsNotNone(current.anonymous_token)

        def test_invalid_session_does_not_reuse_claims(self) -> None:
            """无效签名和跨 namespace Cookie 均不能继承客户端自报身份。"""
            for token in (self.token + 'invalid', ''):
                self.assertNotEqual(self.resolve(109, token).context.user_id, 'original-user')
            self.settings.namespace = 'other-verification'
            self.assertNotEqual(self.resolve(109, self.token).context.user_id, 'original-user')

        def test_expired_history_requires_new_conversation(self) -> None:
            """新 Principal 没有旧 checkpoint 时拒绝旧历史；新对话仅追加新用户消息。"""
            self.assertNotEqual(self.resolve(110, self.token).context.user_id, 'original-user')
            messages = [{'id': 'old', 'role': 'assistant', 'content': 'previous answer'},
                        {'id': 'new', 'role': 'user', 'content': 'new question'}]
            payload = {'threadId': 'test-thread', 'runId': 'test-run', 'state': {}, 'tools': [],
                       'context': [], 'forwardedProps': {}, 'messages': messages}
            with self.assertRaises(RuntimeRequestError) as raised:
                _agent_input(request=parse_run_request(payload), pending=None, history=[])
            self.assertEqual(raised.exception.code, 'untrusted_history')
            fresh = parse_run_request({**payload, 'threadId': 'fresh-thread', 'messages': messages[-1:]})
            self.assertEqual(_agent_input(request=fresh, pending=None, history=[])['messages'], messages[-1:])

    for relative in ('security/tokens.py', 'security/service.py', 'interaction/schemas.py', 'interaction/service.py'):
        source = root / 'src/app' / relative
        print(relative, hashlib.sha256(source.read_bytes()).hexdigest())
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SessionLifecycleTests))
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
