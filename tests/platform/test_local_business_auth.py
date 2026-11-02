"""Offline provider boundary tests; no server, database or external identity calls."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from backend.api.platform.auth.callback import OAuthPurpose, validate_callback
from backend.shared.errors import ApplicationError
from scripts.local_business.auth import (
    CS_ORIGIN,
    PLATFORM_SA,
    TFT_ORIGIN,
    LocalIdentity,
    LocalLogin,
)


@pytest.mark.parametrize(
    "key,value", [("ENVIRONMENT", "production"), ("K_SERVICE", ""), ("K_SERVICE", "worker")]
)
def test_fixture_rejects_production_and_cloud_run(identity, monkeypatch, key, value):
    login = identity.oauth()
    now = datetime.now(UTC)
    _, pending = login.begin(OAuthPurpose.LOGIN, browser_binding="browser", now=now)
    monkeypatch.setenv(key, value)
    operations = [
        lambda: LocalIdentity(identity.auth.parent, identity.web_origin),
        lambda: LocalLogin(identity.web_origin),
        identity.auth_settings,
        identity.oauth,
        lambda: identity.token(CS_ORIGIN, PLATFORM_SA),
        lambda: identity.caller(CS_ORIGIN, PLATFORM_SA),
        lambda: login.begin(OAuthPurpose.LOGIN, browser_binding="browser", now=now),
        lambda: login.redeem(pending, {"code": "local-1"}),
    ]
    for operation in operations:
        with pytest.raises(ValueError, match="LOCAL_IDENTITY_FORBIDDEN_ENVIRONMENT"):
            operation()


@pytest.fixture
def identity(tmp_path):
    auth = tmp_path / "auth"
    auth.mkdir(mode=0o700)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for name, content in {
        "caller-key.pem": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "caller-public.pem": key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    }.items():
        path = auth / name
        path.write_bytes(content)
        path.chmod(0o600)
    return LocalIdentity(tmp_path, "https://localhost:38443")


def test_standard_rsa_verification_and_claim_rejection(identity):
    caller = identity.caller(CS_ORIGIN, PLATFORM_SA)
    token = identity.token(CS_ORIGIN, PLATFORM_SA)
    caller.verify(token)
    with pytest.raises(HTTPException):
        caller.verify(identity.token(TFT_ORIGIN, PLATFORM_SA))
    claims = jwt.decode(token, options={"verify_signature": False})
    for field, value in [("email", "other@example.invalid"), ("exp", 1)]:
        changed = claims | {field: value}
        signed = jwt.encode(changed, identity._key("caller-key.pem"), algorithm="RS256")
        with pytest.raises(HTTPException):
            caller.verify(signed)
    with pytest.raises(HTTPException):
        caller.verify(jwt.encode(claims, "untrusted" * 4, algorithm="HS256"))


def test_no_environment_credentials_or_cloud_key_lookup(identity, monkeypatch):
    monkeypatch.setenv("MICROSOFT_AUTH_CLIENT_SECRET", "must-not-be-used")
    monkeypatch.setenv("MICROSOFT_AUTH_APP_ORIGIN", "https://external.invalid")
    settings = identity.auth_settings()
    settings.validate_configuration(dev_auth_enabled=False)
    assert settings.app_origin == "https://localhost:38443"
    assert settings.client_secret.get_secret_value() == "local-fixture-not-an-external-secret"
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt", lambda *_: pytest.fail())
    identity.caller(CS_ORIGIN, PLATFORM_SA).verify(identity.token(CS_ORIGIN, PLATFORM_SA))


def test_product_callback_binding_and_expiry_remain_required(identity):
    login = identity.oauth()
    now = datetime.now(UTC)
    url, pending = login.begin(OAuthPurpose.LOGIN, browser_binding="browser", now=now)
    assert url.startswith("https://localhost:38443/local/authorize?state=")
    assert len(pending.state) == 43
    for code in ("local-1", "local-2", "local-3", "local-4"):
        params = [("state", pending.state), ("code", code)]
        validated = validate_callback(
            pending,
            params,
            purpose=OAuthPurpose.LOGIN,
            browser_binding="browser",
            session_user_id=None,
            now=now,
        )
        assert str(login.redeem(pending, validated).oid).endswith(code[-1])
    for binding, when in [("wrong", now), ("browser", now + timedelta(minutes=6))]:
        with pytest.raises(ApplicationError):
            validate_callback(
                pending,
                params,
                purpose=OAuthPurpose.LOGIN,
                browser_binding=binding,
                session_user_id=None,
                now=when,
            )
    with pytest.raises(ApplicationError):
        login.redeem(pending, {"code": "external-code"})
    with pytest.raises(HTTPException):
        login.begin(OAuthPurpose.MAILBOX, browser_binding="browser", now=now)


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:38443",
        "https://example.com:38443",
        "https://localhost:38443/",
        "https://localhost:0",
    ],
)
def test_nonlocal_or_noncanonical_origin_denied(tmp_path, origin):
    with pytest.raises(ValueError, match="INVALID_LOCAL_WEB_ORIGIN"):
        LocalIdentity(tmp_path, origin)


def test_publicly_readable_or_symlink_private_key_denied(identity):
    path = identity.auth / "caller-key.pem"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="INVALID_LOCAL_KEY_FILE"):
        identity.token(CS_ORIGIN, PLATFORM_SA)
    path.chmod(0o600)
    target = path.with_suffix(".saved")
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="INVALID_LOCAL_KEY_FILE"):
        identity.token(CS_ORIGIN, PLATFORM_SA)
