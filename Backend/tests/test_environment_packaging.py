"""使用假解释器与临时资源验证启动、打包和前端资源门禁。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]


class EnvironmentPackagingTests(unittest.TestCase):
    def setUp(self) -> None:
        """复制待测脚本到临时工程，替换所有会触发实际构建的命令。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("backend-environment.sh", "start-backend.sh", "build-backend-mac.sh",
                     "mac_pack_arm64.sh", "mac_pack_x64.sh"):
            shutil.copyfile(REPO_ROOT / "scripts" / name, self.scripts / name)
        self.backend = self.root / "Backend"
        self.backend.mkdir()
        self.frontend = self.root / "Frontend"
        (self.frontend / "scripts").mkdir(parents=True)
        shutil.copyfile(REPO_ROOT / "Frontend/scripts/verify-backend-resource.mjs",
                        self.frontend / "scripts/verify-backend-resource.mjs")
        self.log = self.root / "commands.jsonl"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fake_python = self.bin / "python3"
        self.write_command(self.fake_python, '''
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["TEST_COMMAND_LOG"], "a") as log:
    log.write(json.dumps(["python", args, os.getenv("APP_ENV"), os.getenv("DEVAGENTSTUDIO_BACKEND_ENV_FILE"), os.getenv("DEVAGENTSTUDIO_BACKEND_GRAMMARS")]) + "\\n")
if args[:1] == ["-c"]:
    print("3.12")
elif args[:2] == ["-m", "PyInstaller"]:
    dist = pathlib.Path.cwd() / "dist/devagentstudio-backend"
    (dist / "_internal/app/builtin_skills").mkdir(parents=True, exist_ok=True)
    exe = dist / "devagentstudio-backend"
    exe.write_text("fake executable")
    exe.chmod(0o755)
''')
        self.write_command(self.bin / "pnpm", '''
import json, os, sys
with open(os.environ["TEST_COMMAND_LOG"], "a") as log:
    log.write(json.dumps(["pnpm", sys.argv[1:], os.getenv("APP_ENV")]) + "\\n")
''')
        shutil.copyfile(self.fake_python, self.bin / "python3.12")
        (self.bin / "python3.12").chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"APP_ENV", "DEVAGENTSTUDIO_BACKEND_ENV_FILE", "VIRTUAL_ENV"}}
        self.env.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", PYTHON=str(self.fake_python),
                        TEST_COMMAND_LOG=str(self.log))
        self.arch = "arm64" if platform.machine() == "arm64" else "x64"

    def write_command(self, file: Path, body: str) -> None:
        """安装可执行假命令，命令只操作临时测试目录。"""
        file.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        file.chmod(0o755)

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        """从工程外启动脚本以验证绝对路径和参数传递。"""
        return subprocess.run(["bash", str(self.scripts / name), *args], cwd=self.root.parent,
                              env=self.env, text=True, capture_output=True, timeout=15)

    def test_packaging_requires_explicit_valid_environment(self) -> None:
        """即使终端已有 APP_ENV，打包漏传、非法值或未知参数仍失败。"""
        self.env["APP_ENV"] = "prd"
        for name in (f"mac_pack_{self.arch}.sh", "build-backend-mac.sh"):
            for args in ((), ("--env", "bad"), ("--env",), ("--unknown",)):
                with self.subTest(name=name, args=args):
                    result = self.run_script(name, *args)
                    self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_local_explicit_relative_file_is_resolved_before_chdir(self) -> None:
        """启动脚本改变目录后，显式相对配置路径仍指向调用者选择的文件。"""
        file = self.root / "custom.env"
        file.write_text("VALUE=explicit")
        self.env["DEVAGENTSTUDIO_BACKEND_ENV_FILE"] = os.path.relpath(file, self.root.parent)
        result = self.run_script("start-backend.sh", "--env", "st")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(Path(commands[-1][3]), file)

    def test_local_startup_defaults_and_explicit_argument_wins(self) -> None:
        """本地默认 dev，参数覆盖进程 APP_ENV，启动前先检查配置。"""
        for selected, args in (("dev", ()), ("st", ("--env", "st"))):
            with self.subTest(selected=selected):
                (self.backend / (".env" if selected == "dev" else f".env.{selected}")).write_text("VALUE=fake")
                if selected == "st":
                    self.env["APP_ENV"] = "prd"
                result = self.run_script("start-backend.sh", *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertEqual(commands[-2][1], ["-m", "app.environment"])
                self.assertEqual(commands[-1][1][:2], ["-m", "uvicorn"])
                self.assertEqual(commands[-1][2], selected)

    def test_missing_file_fails_before_any_build(self) -> None:
        """存在 dev 文件也不能满足 st 启动或打包。"""
        (self.backend / ".env").write_text("VALUE=dev")
        for name in ("start-backend.sh", "build-backend-mac.sh", f"mac_pack_{self.arch}.sh"):
            result = self.run_script(name, "--env", "st")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing backend configuration for st", result.stderr)
        self.assertFalse(self.log.exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS staging script requires a macOS host")
    def test_all_environment_packages_forward_and_replace_staging(self) -> None:
        """模拟完整一键构建，确认四环境逐层传递且旧配置不残留。"""
        for selected in ("dev", "st", "uat", "prd"):
            with self.subTest(selected=selected):
                filename = ".env" if selected == "dev" else f".env.{selected}"
                (self.backend / filename).write_text(f"VALUE={selected}")
                result = self.run_script(f"mac_pack_{self.arch}.sh", "--env", selected, "--slim")
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertEqual(commands[-1], ["pnpm", [f"build:mac:{self.arch}:{selected}"], selected])
                build = [command for command in commands if command[0] == "python" and command[1][:2] == ["-m", "PyInstaller"]][-1]
                self.assertEqual(build[2], selected)
                self.assertEqual(build[4], "builtin")
                staged = self.frontend / f"resources/backend/darwin-{self.arch}"
                self.assertEqual(sorted(file.name for file in staged.glob(".env*")), [filename])
                self.assertEqual((staged / filename).read_text(), f"VALUE={selected}")

    @unittest.skipUnless(shutil.which("node"), "Node is required for frontend resource validation")
    def test_resource_verifier_rejects_missing_or_mixed_environment(self) -> None:
        """真实运行 Node 资源门禁，拒绝旧配置与目标文件缺失。"""
        staged = self.frontend / "resources/backend/win32"
        (staged / "_internal/app/builtin_skills").mkdir(parents=True)
        (staged / "devagentstudio-backend.exe").write_text("fake")
        script = self.frontend / "scripts/verify-backend-resource.mjs"
        for selected in ("dev", "st", "uat", "prd"):
            filename = ".env" if selected == "dev" else f".env.{selected}"
            file = staged / filename
            file.write_text("VALUE=fake")
            result = subprocess.run(["node", str(script), "--platform=win32", "--arch=x64", f"--env={selected}"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            file.unlink()
        (staged / ".env").write_text("VALUE=dev")
        args = ["node", str(script), "--platform=win32", "--arch=x64", "--env=st"]
        self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
        (staged / ".env.st").write_text("VALUE=st")
        mixed = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(mixed.returncode, 0)
        self.assertIn("Unexpected backend environment files", mixed.stderr)
        self.assertNotEqual(subprocess.run(args[:-1], capture_output=True).returncode, 0)
        (staged / ".env.st").unlink()
        (staged / ".env.st").mkdir()
        invalid_type = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(invalid_type.returncode, 0)
        self.assertIn("Missing packaged backend resources", invalid_type.stderr)


if __name__ == "__main__":
    unittest.main()
