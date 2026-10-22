"""IAM propagation waits must not retry model calls or expose authentication errors."""

import json
from unittest.mock import Mock

import pytest
from google.auth.exceptions import RefreshError

from backend.shared.ai import RuntimeIdentityError, refresh_runtime_credentials


def test_only_iam_permission_propagation_is_retried(monkeypatch):
    error = RefreshError(
        "fixture-secret-must-not-be-printed",
        json.dumps(
            {
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "details": [{"reason": "IAM_PERMISSION_DENIED"}],
                }
            }
        ),
    )
    credentials = Mock()
    credentials.refresh.side_effect = [error, None]
    sleeps = []
    monkeypatch.setattr("backend.shared.ai.time.sleep", sleeps.append)
    refresh_runtime_credentials(credentials, 30)
    assert credentials.refresh.call_count == 2
    assert len(sleeps) == 1


def test_other_auth_errors_fail_without_retry_or_secret_exposure(monkeypatch):
    credentials = Mock()
    credentials.refresh.side_effect = RefreshError("fixture-secret-must-not-be-printed")
    sleep = Mock()
    monkeypatch.setattr("backend.shared.ai.time.sleep", sleep)
    with pytest.raises(RuntimeIdentityError) as caught:
        refresh_runtime_credentials(credentials, 30)
    assert "fixture-secret" not in str(caught.value)
    assert credentials.refresh.call_count == 1
    sleep.assert_not_called()
