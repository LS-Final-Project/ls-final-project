from eval.smoke import FakeProvider, Usage, evaluate


def test_fake_is_distinguished_from_measured_model_quality_and_unknown_cost():
    report = evaluate(FakeProvider(), "fake", "fake-no-api")
    assert report["quality_gate_evaluated"] is False
    assert report["data_origin"] == "synthetic"
    assert len(report["cases"]) == 2
    assert all(row["schema_valid"] for row in report["cases"])
    assert all(row["cost_usd"] is None for row in report["cases"])


def test_failed_schema_preserves_usage_and_sensitive_provider_errors_are_redacted():
    class InvalidSchema:
        def generate(self, prompt):
            return "not JSON", Usage(input_tokens=10, output_tokens=4)

    report = evaluate(InvalidSchema(), "fixture", "fixture")
    assert not report["cases"][0]["schema_valid"]
    assert report["cases"][0]["usage"]["input_tokens"] == 10

    class FailedProvider:
        def generate(self, prompt):
            raise RuntimeError("fixture-sensitive-provider-message")

    report = evaluate(FailedProvider(), "fixture", "fixture")
    assert "fixture-sensitive" not in str(report)
    assert report["cases"][0]["error_code"] == "RuntimeError"

    class ProviderError(Exception):
        code = 429

    class QuotaFailure:
        def generate(self, prompt):
            raise ProviderError("fixture-sensitive-key")

    report = evaluate(QuotaFailure(), "fixture", "fixture")
    assert report["cases"][0]["http_status"] == 429
    assert "fixture-sensitive-key" not in str(report)
