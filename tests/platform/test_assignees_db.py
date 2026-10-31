"""KAN-195 담당자 대상 검증 SQL을 실제 identity 표에서 확인한다(전용 PostgreSQL)."""

import asyncio
from uuid import uuid4

import psycopg
import pytest

from backend.api.platform.assignees.routes import validate_owner
from backend.shared.access import Principal
from backend.shared.errors import ApplicationError

CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})


def body(owner):
    return f'{{"version": 1, "owner_user_id": "{owner}"}}'.encode()


def test_only_an_active_cs_agent_is_a_valid_owner(settings, auth_database, registered_user):
    """활성 cs_agent만 통과하고, 비활성·다른 역할·없는 사용자는 422 CS_INVALID_OWNER."""
    agent = registered_user[1]
    other_role, inactive = uuid4(), uuid4()
    with psycopg.connect(auth_database) as conn:
        for user, active in ((other_role, True), (inactive, False)):
            conn.execute(
                "INSERT INTO identity.users(id,tid,oid,email,display_name,dept,is_active) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (user, uuid4(), uuid4(), f"{user}@example.invalid", "합성", "TS_AUTO", active),
            )
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'dept_staff'), (%s,'cs_agent')",
            (other_role, inactive),
        )
    try:
        asyncio.run(validate_owner(settings, CS_MANAGER, body(agent)))
        for owner in (other_role, inactive, uuid4()):
            with pytest.raises(ApplicationError) as error:
                asyncio.run(validate_owner(settings, CS_MANAGER, body(owner)))
            assert (error.value.status_code, error.value.code) == (422, "CS_INVALID_OWNER")
    finally:
        with psycopg.connect(auth_database) as conn:
            conn.execute(
                "DELETE FROM identity.user_roles WHERE user_id = ANY(%s)", ([other_role, inactive],)
            )
            conn.execute("DELETE FROM identity.users WHERE id = ANY(%s)", ([other_role, inactive],))
