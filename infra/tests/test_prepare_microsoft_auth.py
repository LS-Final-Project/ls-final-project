"""가짜 gcloud로 공용 인증 준비의 비활성 기본값·대상 제한·비밀값 보존을 검증한다."""

import contextlib
import importlib.util
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "prepare_microsoft_auth", Path(__file__).parents[1] / "prepare_microsoft_auth.py"
)
preparation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparation)
CLIENT = "00000000-0000-4000-8000-000000000001"
SECRET = "fixture-sensitive-ms-secret"


def service_response(changes=None, account=None):
    """실제 값 없는 플랫폼 설정 응답을 만든다.

    Args:
        changes: 경계 검사를 위해 바꿀 환경 설정 항목.
        account: 검사할 실행 계정. 없으면 정상 플랫폼 계정.

    Returns:
        str: 가짜 gcloud의 서비스 설정 JSON.
    """
    values = {
        "ENVIRONMENT": {"value": "production"},
        "DEV_AUTH_ENABLED": {"value": "false"},
    } | (changes or {})
    return json.dumps(
        {
            "spec": {
                "template": {
                    "spec": {
                        "serviceAccountName": account or preparation.RUNTIME,
                        "containers": [
                            {"env": [{"name": name, **value} for name, value in values.items()]}
                        ],
                    }
                }
            }
        }
    )


class MicrosoftPreparationTests(unittest.TestCase):
    """실제 클라우드 호출 없이 준비 명령의 동작·보존 조건을 확인한다."""

    def test_preview_and_invalid_input_do_not_call_cloud(self):
        """기본 미리보기와 잘못된 자격증명은 외부 호출하지 않는지 확인한다.

        Args:
            없음.

        Returns:
            None: 미리보기·입력 검증과 비밀값 비노출을 검증한다.
        """
        output = io.StringIO()
        with patch.object(preparation, "gcloud") as cloud, contextlib.redirect_stdout(output):
            preparation.prepare(CLIENT, SECRET)
            for client, secret in (("bad-id", SECRET), (CLIENT, " "), (CLIENT, "x" * 65537)):
                with self.assertRaises(preparation.PreparationError):
                    preparation.prepare(client, secret, apply=True)
        cloud.assert_not_called()
        self.assertNotIn(SECRET, output.getvalue())
        self.assertIn("MICROSOFT_AUTH_ENABLED=false", output.getvalue())

    def test_apply_uses_stdin_and_only_prepares_disabled_platform_auth(self):
        """비밀값을 argv에 넣지 않고 플랫폼 한 곳의 인증을 비활성으로 준비하는지 확인한다.

        Args:
            없음.

        Returns:
            None: 업로드·접근 권한 대상과 비활성 서버 설정을 검증한다.
        """
        responses = [service_response().encode(), b"", b"", b"", b""]
        with (
            patch.object(
                preparation.subprocess,
                "run",
                side_effect=[subprocess.CompletedProcess([], 0, value, b"") for value in responses],
            ) as run,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            preparation.prepare(CLIENT, SECRET, apply=True)
        calls = run.call_args_list
        uploads = [call for call in calls if call.kwargs.get("input") is not None]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].kwargs["input"], SECRET.encode())
        commands = [call.args[0] for call in calls]
        self.assertNotIn(SECRET, " ".join(word for command in commands for word in command))
        self.assertNotIn(SECRET, output.getvalue())
        grants = [
            command for command in commands if command[1:3] == ["secrets", "add-iam-policy-binding"]
        ]
        self.assertEqual(len(grants), 1)
        self.assertIn(f"--member=serviceAccount:{preparation.RUNTIME}", grants[0])
        update = commands[-1]
        self.assertEqual(update[1:5], ["run", "services", "update", "team4-platform"])
        env = next(word for word in update if word.startswith("--update-env-vars="))
        self.assertIn("MICROSOFT_AUTH_ENABLED=false", env)
        self.assertIn("DEV_AUTH_ENABLED=false", env)
        self.assertNotIn("CLIENT_SECRET", env)
        self.assertIn(
            "--update-secrets=MICROSOFT_AUTH_CLIENT_SECRET=microsoft-client-secret:1", update
        )
        for command in commands:
            self.assertIn(f"--project={preparation.PROJECT}", command)
            self.assertIn(f"--account={preparation.ACCOUNT}", command)

    def test_active_or_conflicting_service_is_preserved_before_any_secret_mutation(self):
        """이미 활성화됐거나 다른 앱·Secret 연결인 서비스를 변경하지 않는지 확인한다.

        Args:
            없음.

        Returns:
            None: 충돌을 읽기 단계에서 거부해 Secret·IAM·서버 설정을 보존함을 검증한다.
        """
        configurations = [
            service_response({"MICROSOFT_AUTH_ENABLED": {"value": "true"}}),
            service_response({"DEV_AUTH_ENABLED": {"value": "true"}}),
            service_response({"MICROSOFT_AUTH_ENABLED": {"valueFrom": {}}}),
            service_response({"ENVIRONMENT": {"value": "local"}}),
            service_response({"MICROSOFT_AUTH_CLIENT_ID": {"value": "other-app"}}),
            service_response({"MICROSOFT_AUTH_CLIENT_SECRET": {"value": SECRET}}),
            service_response(account="another-runtime@example.invalid"),
        ]
        for configuration in configurations:
            with (
                self.subTest(configuration=configuration),
                patch.object(preparation, "gcloud", return_value=configuration) as cloud,
            ):
                with self.assertRaises(preparation.PreparationError):
                    preparation.prepare(CLIENT, SECRET, apply=True)
                self.assertEqual(cloud.call_count, 1)

    def test_existing_version_is_verified_and_never_replaced(self):
        """기존 Secret은 활성 버전·값 일치를 확인하고 덮어쓰거나 버전을 추가하지 않는지 확인한다.

        Args:
            없음.

        Returns:
            None: 동일 값의 재사용과 다른 값·비활성 버전 거부를 검증한다.
        """
        for state, stored, succeeds in (
            ("ENABLED", SECRET, True),
            ("ENABLED", "other", False),
            ("DISABLED", SECRET, False),
        ):
            with (
                self.subTest(state=state, succeeds=succeeds),
                patch.object(
                    preparation,
                    "gcloud",
                    side_effect=[service_response(), preparation.SECRET, state, stored, "", ""],
                ) as cloud,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                if succeeds:
                    preparation.prepare(CLIENT, SECRET, apply=True)
                else:
                    with self.assertRaises(preparation.PreparationError):
                        preparation.prepare(CLIENT, SECRET, apply=True)
                commands = [call.args[0] for call in cloud.call_args_list]
                self.assertFalse(any(command[:2] == ["secrets", "create"] for command in commands))
                self.assertFalse(any(call.kwargs.get("payload") for call in cloud.call_args_list))
                if not succeeds:
                    self.assertFalse(
                        any(
                            command[:2] == ["secrets", "add-iam-policy-binding"]
                            for command in commands
                        )
                    )
                self.assertNotIn(SECRET, output.getvalue())

    def test_cloud_failure_and_timeout_do_not_expose_provider_output(self):
        """실패 출력이나 시간 초과의 비밀값을 사용자 오류에 포함하지 않는지 확인한다.

        Args:
            없음.

        Returns:
            None: 원시 출력 비노출과 자동 재시도 없음을 검증한다.
        """
        failures = [
            {"return_value": subprocess.CompletedProcess([], 1, SECRET.encode(), SECRET.encode())},
            {"side_effect": subprocess.TimeoutExpired(["gcloud"], 120, output=SECRET.encode())},
        ]
        for failure in failures:
            with (
                self.subTest(failure=type(failure)),
                patch.object(preparation.subprocess, "run", **failure) as run,
            ):
                with self.assertRaises(preparation.PreparationError) as rejected:
                    preparation.gcloud(["secrets", "list"])
                self.assertNotIn(SECRET, str(rejected.exception))
                self.assertEqual(run.call_count, 1)
