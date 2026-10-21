"""Small synthetic pipeline check with swappable providers and recorded usage."""

import argparse
import html
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from dotenv import dotenv_values
from pydantic import BaseModel


class Prediction(BaseModel):
    result: Literal["ready"]


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None


class Provider(Protocol):
    def generate(self, prompt: str) -> tuple[str, Usage]: ...


class FakeProvider:
    def generate(self, prompt: str) -> tuple[str, Usage]:
        return '{"result":"ready"}', Usage(0, 0, 0, 0, 0)


class GeminiProvider:
    def __init__(self, model: str, key: str) -> None:
        from google import genai
        from google.genai import types

        self.client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=30000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )
        self.model = model

    def generate(self, prompt: str) -> tuple[str, Usage]:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Prediction,
                max_output_tokens=512,
            ),
        )
        raw = response.usage_metadata
        usage = (
            Usage(
                raw.prompt_token_count,
                raw.candidates_token_count,
                raw.thoughts_token_count,
                raw.cached_content_token_count,
                raw.total_token_count,
            )
            if raw
            else Usage()
        )
        return response.text or "", usage


CASES = (
    ("en", 'Return the JSON object {"result":"ready"}. This is a synthetic setup check.'),
    ("ko", '합성 연결 점검입니다. JSON {"result":"ready"}만 반환하세요.'),
)


def evaluate(provider: Provider, name: str, model: str) -> dict:
    rows = []
    for case_id, prompt in CASES:
        started = time.perf_counter()
        usage = Usage()
        parsed = None
        error_code = None
        http_status = None
        try:
            raw, usage = provider.generate(prompt)
            parsed = Prediction.model_validate_json(raw).model_dump()
        except Exception as error:
            # Exceptions may contain requests or credentials. Never persist their messages.
            error_code = type(error).__name__
            status = getattr(error, "code", None)
            http_status = status if isinstance(status, int) else None
        rows.append(
            {
                "case_id": case_id,
                "attempt": 1,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "schema_valid": parsed is not None,
                "prediction": parsed,
                "error_code": error_code,
                "http_status": http_status,
                "usage": asdict(usage),
                "cost_usd": None,
            }
        )
    return {
        "run_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "provider": name,
        "model": model,
        "data_origin": "synthetic",
        "dataset_version": "inline-setup-smoke-v1",
        "prompt_version": "setup-smoke-v1",
        "purpose": "connection_and_recording_only",
        "quality_gate_evaluated": False,
        "pricing": None,
        "cases": rows,
    }


def save(report: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report['run_id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = "".join(
        f"<tr><td>{html.escape(row['case_id'])}</td><td>{row['schema_valid']}</td>"
        f"<td>{row['latency_ms']}</td><td>{row['usage']['input_tokens']}</td>"
        f"<td>{row['usage']['output_tokens']}</td><td>{row['usage']['thought_tokens']}</td>"
        f"<td>{html.escape(row['error_code'] or '')}</td><td>{row.get('http_status') or ''}</td></tr>"
        for row in report["cases"]
    )
    page = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>평가 연결 점검</title>
<style>body{{font-family:system-ui;margin:40px;line-height:1.7}}table{{border-collapse:collapse}}th,td{{padding:10px;border:1px solid #ccd}}</style>
<h1>평가 연결 점검</h1><p>제공자: {html.escape(report["provider"])} / 모델: {html.escape(report["model"])}</p>
<p>합성 입력 2건의 호출과 기록 경로를 확인합니다. 실제 업무 성능의 합격 판정은 수행하지 않았습니다.</p>
<p>가격표가 등록되지 않아 비용은 미산정입니다. None은 제공되지 않은 사용량입니다.</p>
<table><tr><th>사례</th><th>스키마</th><th>지연 ms</th><th>입력 토큰</th><th>출력 토큰</th><th>추론 토큰</th><th>오류</th><th>HTTP</th></tr>{rows}</table></html>"""
    path.with_suffix(".html").write_text(page, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("fake", "gemini"), default="fake")
    parser.add_argument("--model")
    parser.add_argument("--env-file", type=Path, default=Path(".env.integrations"))
    args = parser.parse_args()
    provider: Provider
    if args.provider == "fake":
        provider = FakeProvider()
    else:
        values = dotenv_values(args.env_file) if args.env_file.exists() else {}
        key = os.environ.get("GEMINI_API_KEY") or values.get("GEMINI_API_KEY")
        if not key or not args.model:
            parser.error(
                "Gemini requires a local GEMINI_API_KEY and explicit --model. Never paste the key into chat."
            )
        provider = GeminiProvider(args.model, key)
    report = evaluate(provider, args.provider, args.model or "fake-no-api")
    path = save(report, Path(__file__).resolve().parents[1] / "artifacts/eval")
    print(f"REPORT: {path}\nVIEW: {path.with_suffix('.html')}")
    return 0 if all(row["schema_valid"] for row in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
