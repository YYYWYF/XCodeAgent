"""验证错误配置不会将服务端凭证带入启动日志。"""

import unittest

from pydantic import ValidationError

from app.config import Settings


class ConfigSafetyTests(unittest.TestCase):
    def test_validation_error_hides_raw_configuration(self):
        """错误地址与模型校验失败都隐藏原始输入中的密钥。"""
        for overrides in ({"direct_base_url": "invalid"}, {"default_timeout_seconds": -1}):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError) as captured:
                Settings(_env_file=None, direct_api_key="FAKE_CONFIG_SECRET",
                         service_token="FAKE_SERVICE_TOKEN", **overrides)
            diagnostic = str(captured.exception)
            self.assertNotIn("FAKE_CONFIG_SECRET", diagnostic)
            self.assertNotIn("FAKE_SERVICE_TOKEN", diagnostic)
            self.assertNotIn("input_value", diagnostic)


if __name__ == "__main__":
    unittest.main()
