"""KAN-155 측정 환경 설정 변환. 운영 이름·주소·DB가 남지 않는지 본다."""

import json
import unittest

from infra import load_env as e

PROD_SQL = f"/cloudsql/{e.PROJECT}:{e.REGION}:team4-sql"


def service(name, env):
    return {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {
            "name": f"team4-{name}",
            "uid": "x",
            "resourceVersion": "1",
            "labels": {"commit-sha": "abc", "cloud.googleapis.com/location": e.REGION},
            "annotations": {
                "run.googleapis.com/ingress": "all",
                "serving.knative.dev/creator": "me",
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "name": f"team4-{name}-00042-abc",
                    "annotations": {
                        "run.googleapis.com/cloudsql-instances": f"{e.PROJECT}:{e.REGION}:team4-sql"
                    },
                },
                "spec": {
                    "containerConcurrency": 20,
                    "containers": [{"image": "img@sha256:1", "env": env}],
                },
            },
            "traffic": [{"revisionName": f"team4-{name}-00042-abc", "percent": 100}],
        },
        "status": {"url": e.url(name, False)},
    }


class LoadEnvTest(unittest.TestCase):
    def test_platform_copy_points_at_load_services_db_and_secrets(self):
        env = [
            {"name": "PGHOST", "value": PROD_SQL},
            {"name": "BUSINESS_CS_URL", "value": e.url("cs", False)},
            {"name": "CS_JOBS_SERVICE_URL", "value": e.url("platform", False)},
            {"name": "MAIL_SYNC_ENABLED", "value": "true"},
            {
                "name": "PGPASSWORD",
                "valueFrom": {"secretKeyRef": {"name": "platform-db-password", "key": "1"}},
            },
            {
                "name": "MICROSOFT_AUTH_CLIENT_SECRET",
                "valueFrom": {"secretKeyRef": {"name": "microsoft-client-secret", "key": "1"}},
            },
        ]
        out = e.service_spec(service("platform", env), "platform")
        values = {
            i["name"]: i.get("value", i.get("valueFrom"))
            for i in out["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(out["metadata"]["name"], "team4-platform-load")
        self.assertEqual(values["PGHOST"], PROD_SQL + "-load")
        self.assertEqual(values["BUSINESS_CS_URL"], e.url("cs", True))
        self.assertEqual(values["CS_JOBS_SERVICE_URL"], e.url("platform", True))
        self.assertEqual(values["MAIL_SYNC_ENABLED"], "false")
        self.assertEqual(values["TFT_WORKER_JOBS_JOB"], "tft-worker-load")
        self.assertEqual(values["MICROSOFT_AUTH_APP_ORIGIN"], e.url("web", True))
        self.assertEqual(
            values["PGPASSWORD"],
            {"secretKeyRef": {"name": "platform-db-password-load", "key": "latest"}},
        )
        self.assertEqual(
            values["MICROSOFT_AUTH_CLIENT_SECRET"]["secretKeyRef"]["name"],
            "microsoft-client-secret",
        )
        template = out["spec"]["template"]
        self.assertNotIn("name", template["metadata"])
        self.assertTrue(
            template["metadata"]["annotations"]["run.googleapis.com/cloudsql-instances"].endswith(
                ":team4-sql-load"
            )
        )
        self.assertEqual(template["spec"]["containerConcurrency"], 20)
        self.assertEqual(out["spec"]["traffic"], [{"latestRevision": True, "percent": 100}])
        self.assertNotIn("status", out)
        self.assertNotIn("uid", out["metadata"])
        self.assertEqual(out["metadata"]["labels"], {"commit-sha": "abc", "purpose": "kan155-load"})
        self.assertNotIn("serving.knative.dev/creator", out["metadata"]["annotations"])
        # 운영 주소·인스턴스가 한 글자도 남지 않는다.
        text = json.dumps(out)
        for name in e.SERVICES:
            self.assertNotIn(e.url(name, False) + '"', text)
        self.assertNotIn(':team4-sql"', text)

    def test_concurrency_override_touches_only_web_and_platform(self):
        for name, expected in (("platform", 80), ("web", 80), ("cs", 20)):
            out = e.service_spec(service(name, []), name, concurrency=80)
            self.assertEqual(out["spec"]["template"]["spec"]["containerConcurrency"], expected)

    def test_worker_job_mode_sets_the_rfp_model(self):
        spec = {
            "apiVersion": "run.googleapis.com/v1",
            "kind": "Job",
            "metadata": {"name": "tft-worker"},
            "spec": {
                "template": {
                    "metadata": {},
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "env": [
                                            {
                                                "name": "DATABASE_URL",
                                                "value": f"postgresql:///team4?host={PROD_SQL}&user=tft_worker_login",
                                            },
                                            {"name": "TFT_DOCUMENT_BUCKET", "value": e.BUCKET},
                                            {"name": "TFT_EXTRACTION_PROVIDER", "value": "gemini"},
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                }
            },
        }
        for mode, provider in (("a", "fake"), ("b", "gemini")):
            out = e.job_spec(spec, "tft", mode)
            env = {
                i["name"]: i["value"]
                for i in out["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            self.assertEqual(out["metadata"]["name"], "tft-worker-load")
            self.assertEqual(env["TFT_EXTRACTION_PROVIDER"], provider)
            self.assertEqual(env["TFT_DOCUMENT_BUCKET"], e.BUCKET_LOAD)
            self.assertIn(":team4-sql-load&user=tft_worker_login", env["DATABASE_URL"])

    def test_rewrite_leaves_other_names_alone(self):
        self.assertEqual(e.rewrite("team4-sql-replica"), "team4-sql-replica")
        self.assertEqual(e.rewrite(e.BUCKET + "-other"), e.BUCKET + "-other")

    def test_teardown_only_names_load_resources(self):
        for command in e.teardown_commands("someone@example.com"):
            self.assertTrue(any("-load" in part for part in command))


if __name__ == "__main__":
    unittest.main()
