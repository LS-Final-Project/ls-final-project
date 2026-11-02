"""Run one regular worker with local admission/accounting, never a provider USD hard cap.

The parent owns grants and supplies a clean local environment. Only the SDK factory and
the claim observer are intercepted; product graphs, DB leases and results stay regular.
Unknown spend retains its reservation and escapes Exception-based product fallbacks.
"""

import os
import runpy
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from backend.shared import ai


class LocalStop(BaseException):
    """A fixed local reason that must not become a successful product fallback."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Configuration:
    owner: str
    job_id: UUID
    token: str
    control: Path
    key_file: Path
    prices_file: Path


def configuration() -> Configuration:
    """Validate parent-supplied local intent before opening keys, journals or clients."""
    env = os.environ
    owner = env.get("LOCAL_JOB_OWNER")
    if (
        env.get("ENVIRONMENT") != "local"
        or "K_SERVICE" in env
        or env.get("LOCAL_BUSINESS_MODE") != "real"
        or owner not in {"cs", "tft"}
        or env.get("GEMINI_TRANSIENT_RETRIES") != "0"
        or env.get("CS_VECTOR_SEARCH_ENABLED") != "false"
        or not env.get("DATABASE_URL")
        or Path(".env").exists()
    ):
        raise LocalStop("LOCAL_WORKER_CONFIGURATION_INVALID")
    try:
        job = UUID(env["LOCAL_JOB_ID"])
        if str(job) != env["LOCAL_JOB_ID"] or env.get(f"{owner.upper()}_JOB_ID") != str(job):
            raise ValueError
        if env.get(f"{owner.upper()}_WORKER_ENABLED") not in {"1", "true"}:
            raise ValueError
        values = [
            env[name]
            for name in (
                "LOCAL_DRIVER_TOKEN",
                "LOCAL_BUSINESS_CONTROL",
                "LOCAL_MODEL_KEY_FILE",
                "LOCAL_PRICES_FILE",
            )
        ]
        if any(not value.strip() for value in values):
            raise ValueError
        if any(not Path(value).is_absolute() for value in values[1:]):
            raise ValueError
    except (KeyError, ValueError):
        raise LocalStop("LOCAL_WORKER_CONFIGURATION_INVALID") from None
    return Configuration(owner, job, values[0], *(Path(value) for value in values[1:]))


def complete_usage(response) -> ai.Usage:
    """Require all billable components; a partial estimate_cost total is not known spend."""
    raw = response.usage_metadata
    values = [
        getattr(raw, name, None)
        for name in (
            "prompt_token_count",
            "candidates_token_count",
            "thoughts_token_count",
            "total_token_count",
        )
    ]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("LOCAL_USAGE_INCOMPLETE")
    # Shared Usage contract: Gemini omits cachedContentTokenCount for an uncached call.
    cached = getattr(raw, "cached_content_token_count", None)
    cached = 0 if cached is None else cached
    if type(cached) is not int or not 0 <= cached <= values[0]:
        raise ValueError("LOCAL_USAGE_INCOMPLETE")
    if values[3] != sum(values[:3]):
        raise ValueError("LOCAL_USAGE_INCOMPLETE")
    return ai.Usage(values[0], values[1], values[2], cached, values[3])


class Meter:
    """One shared gate for every client/model created during the regular worker run."""

    def __init__(self, permit, config: Configuration, prices: dict[str, ai.ModelPrice]):
        self.permit, self.config, self.prices = permit, config, prices
        self.stopped: str | None = None

    def stop(self, code: str) -> None:
        self.stopped = code
        raise LocalStop(code) from None

    def priced(self, model) -> str:
        if self.stopped:
            self.stop(self.stopped)
        price = self.prices.get(model) if isinstance(model, str) else None
        if (
            price is None
            or price.currency != "USD"
            or any(
                not value.is_finite() or value < 0
                for value in (
                    price.input_per_million,
                    price.output_per_million,
                    price.cached_input_per_million,
                )
            )
        ):
            self.stop("LOCAL_MODEL_PRICE_REQUIRED")
        return model

    def unknown(self, charge: str, code: str) -> None:
        try:
            self.permit.settle(charge, actual_usd=None)
        except Exception:
            # The outstanding reservation still blocks later calls if settlement failed.
            self.stop("LOCAL_SETTLEMENT_FAILED")
        self.stop(code)

    def generate(self, generate_content, *args, **kwargs):
        if args:
            self.stop("LOCAL_SDK_OPERATION_BLOCKED")
        model = self.priced(kwargs.get("model"))
        try:
            charge = self.permit.reserve(self.config.token, str(self.config.job_id), model)
        except Exception:
            self.stop("LOCAL_RESERVATION_DENIED")
        try:
            response = generate_content(**kwargs)
        except BaseException:
            self.unknown(charge, "LOCAL_PROVIDER_CALL_FAILED")
        try:
            usage = complete_usage(response)
            cost = ai.estimate_cost(usage, model, self.prices)
            parts = (cost.input, cost.output, cost.cached_input, cost.thought, cost.total)
            if cost.currency != "USD" or any(
                part is None or not part.is_finite() or part < 0 for part in parts
            ):
                raise ValueError("LOCAL_COST_INCOMPLETE")
        except Exception:
            self.unknown(charge, "LOCAL_COST_UNKNOWN")
        try:
            reason = self.permit.settle(
                charge,
                actual_usd=str(cost.total),
                usage={
                    name: getattr(usage, name)
                    for name in (
                        "input_tokens",
                        "output_tokens",
                        "thought_tokens",
                        "cached_input_tokens",
                        "total_tokens",
                    )
                },
            )
        except Exception:
            self.stop("LOCAL_SETTLEMENT_FAILED")
        if reason is not None:
            self.stop(
                "LOCAL_RESERVATION_EXCEEDED"
                if reason == "LOCAL_RESERVATION_EXCEEDED"
                else "LOCAL_COST_UNKNOWN"
            )
        return response


class Models:
    def __init__(self, client, meter: Meter):
        self._client, self._meter = client, meter

    def generate_content(self, *args, **kwargs):
        return self._meter.generate(self._client.models.generate_content, *args, **kwargs)

    def __getattr__(self, name):
        self._meter.stop("LOCAL_SDK_OPERATION_BLOCKED")


class Client:
    def __init__(self, client, meter: Meter):
        self.models = Models(client, meter)
        self._meter = meter

    def __getattr__(self, name):
        # No aio, embeddings, streaming, files, batches or underlying HTTP transport.
        self._meter.stop("LOCAL_SDK_OPERATION_BLOCKED")


@contextmanager
def intercept(meter: Meter, key: SecretStr, runtime, claimed: list):
    factory, claim = ai.create_client, runtime.claim

    def create(settings, *, credentials=None, embedding=False):
        if credentials is not None or embedding or settings.ai_provider != "gemini":
            meter.stop("LOCAL_SDK_OPERATION_BLOCKED")
        if settings.gemini_transient_retries != 0:
            meter.stop("LOCAL_RETRIES_BLOCKED")
        meter.priced(settings.gemini_model)
        try:
            return Client(factory(settings.model_copy(update={"gemini_api_key": key})), meter)
        except Exception:
            meter.stop("LOCAL_CLIENT_CREATION_FAILED")

    def observe(settings, owner, lease_seconds, job_id):
        if owner != meter.config.owner or job_id != meter.config.job_id or claimed:
            meter.stop("LOCAL_JOB_SCOPE_MISMATCH")
        lease = claim(settings, owner, lease_seconds, job_id)
        if isinstance(lease, runtime.Lease):
            claimed.append((settings, lease))
        return lease

    ai.create_client, runtime.claim = create, observe
    try:
        yield
    finally:
        ai.create_client, runtime.claim = factory, claim


def fail_claimed(runtime, claimed: list, code: str) -> None:
    """Only finish our observed claim; never query, reacquire or steal another lease."""
    if not claimed:
        print("LOCAL_FAILURE_NOT_RECORDED_NO_LEASE", file=sys.stderr)
        return
    settings, lease = claimed[0]
    try:
        runtime.finish(settings, lease, "FAILED", {}, failure_code=code)
    except runtime.LeaseLost:
        print("LOCAL_FAILURE_NOT_RECORDED_LEASE_LOST", file=sys.stderr)
    except Exception:
        print("LOCAL_FAILURE_NOT_RECORDED", file=sys.stderr)


def main() -> None:
    claimed: list = []
    runtime = None
    try:
        config = configuration()
        from backend.agent import job_runtime
        from scripts.local_business.permit import Permit

        runtime = job_runtime
        module = f"backend.agent.{config.owner}_worker"
        if module in sys.modules:
            raise LocalStop("LOCAL_WORKER_ALREADY_IMPORTED")
        prices = ai.load_price_table(config.prices_file)
        key = SecretStr(config.key_file.read_text().strip())
        if not key.get_secret_value():
            raise LocalStop("LOCAL_MODEL_KEY_REQUIRED")
        meter = Meter(Permit(config.control / "permit.sqlite3"), config, prices)
        with intercept(meter, key, runtime, claimed):
            runpy.run_module(module, run_name="__main__")
    except LocalStop as error:
        if runtime is not None:
            fail_claimed(runtime, claimed, error.code)
        raise SystemExit(error.code) from None
    except Exception:
        if runtime is not None:
            fail_claimed(runtime, claimed, "LOCAL_WORKER_BOOTSTRAP_FAILED")
        raise SystemExit("LOCAL_WORKER_BOOTSTRAP_FAILED") from None


if __name__ == "__main__":
    main()
