"""가상 협력사 프로필 시드(KAN-184). profiles.json을 tft.partner_profiles 행으로 옮기고 파일과 대조한다.

RFP 처리 함수는 협력사 등급을 DB 행(FK)으로만 매기므로 프로필 행이 없으면 등급 단계를 건너뛴다. 파일
(backend/api/tft/partners/profiles.json, loader.load_profiles)이 원천이고, 이 스크립트가 그대로 넣는다.

기본은 대조만 한다(--check와 같음, DB 읽기). 후보·판본마다 SAME(같음)·MISSING(행 없음)·DIFFERENT(같은 판본인데
내용이 다름)를 내고 MISSING·DIFFERENT가 있으면 종료 1이다. --execute는 INSERT … ON CONFLICT DO NOTHING으로 없는
판본만 넣고 다시 대조한다. 같은 판본의 내용이 바뀌었으면 덮어쓰지 않으니 파일의 profile_version을 올린다.

DSN은 환경변수 DATABASE_URL(tft_app, 로컬 DB)에서만 읽고 출력하지 않는다. 원격 DB는 거부한다.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier, Placeholder

from backend.api.tft.partners.loader import PROFILES, load_profiles, profile_from_row, profile_row
from backend.api.tft.partners.schemas import PartnerProfile
from scripts.cs_index_docs import is_local


def compare(
    conn: psycopg.Connection[dict[str, Any]], profiles: list[PartnerProfile]
) -> list[tuple[str, int, str]]:
    """파일의 후보·판본마다 DB 행과 대조한다.

    Args:
        conn: dict_row 연결.
        profiles: 파일 프로필.

    Returns:
        list[tuple[str, int, str]]: (candidate_code, profile_version, SAME|MISSING|DIFFERENT), 파일 순서.
    """
    rows = {
        (row["candidate_code"], row["profile_version"]): row
        for row in conn.execute("SELECT * FROM tft.partner_profiles").fetchall()
    }
    result = []
    for profile in profiles:
        row = rows.get((profile.candidate_code, profile.profile_version))
        status = (
            "MISSING"
            if row is None
            else "SAME"
            if profile_from_row(row) == profile
            else "DIFFERENT"
        )
        result.append((profile.candidate_code, profile.profile_version, status))
    return result


def insert(conn: psycopg.Connection[dict[str, Any]], profiles: list[PartnerProfile]) -> int:
    """없는 판본만 넣는다(있는 판본은 내용이 달라도 그대로 둔다).

    Args:
        conn: 연결.
        profiles: 파일 프로필.

    Returns:
        int: 새로 넣은 행 수.
    """
    inserted = 0
    with conn.transaction():
        for profile in profiles:
            row = profile_row(profile)
            cursor = conn.execute(
                SQL(
                    "INSERT INTO tft.partner_profiles ({}) VALUES ({}) ON CONFLICT DO NOTHING"
                ).format(
                    SQL(", ").join(map(Identifier, row)),
                    SQL(", ").join(Placeholder() * len(row)),
                ),
                list(row.values()),
            )
            inserted += cursor.rowcount
    return inserted


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 대조하거나(--execute면 넣은 뒤) 표와 요약을 출력한다.

    Args:
        argv: 명령행 인자. None이면 sys.argv.

    Returns:
        int: 모두 SAME이면 0, MISSING·DIFFERENT가 있거나 DSN·파일이 잘못되면 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=PROFILES)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="대조만(기본)")
    mode.add_argument("--execute", action="store_true", help="없는 판본을 넣고 대조")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn or not is_local(dsn):
        print(
            "TFT_SEED_NEEDS_LOCAL_DB: DATABASE_URL(tft_app, 로컬 DB)이 필요하다.", file=sys.stderr
        )
        return 1
    try:
        profiles = load_profiles(args.profiles)
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            inserted = insert(conn, profiles) if args.execute else 0
            statuses = compare(conn, profiles)
    except (OSError, ValueError, psycopg.Error):
        # pydantic.ValidationError는 ValueError다. DSN·원문은 출력하지 않는다.
        print("TFT_SEED_FAILED: 프로필 파일과 DB 연결을 확인한다.", file=sys.stderr)
        return 1
    for code, version, status in statuses:
        print(f"{code}\tv{version}\t{status}")
    summary = {"inserted": inserted} | {
        status: sum(s == status for *_, s in statuses)
        for status in ("SAME", "MISSING", "DIFFERENT")
    }
    print(json.dumps(summary))
    return 0 if summary["MISSING"] == summary["DIFFERENT"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
