"""가상 협력사 프로필 시드(KAN-184). profiles.json을 tft.partner_profiles 행으로 옮기고 파일과 대조한다.

RFP 처리 함수는 협력사 등급을 DB 행(FK)으로만 매기므로 프로필 행이 없으면 등급 단계를 건너뛴다. 파일
(backend/api/tft/partners/profiles.json, loader.load_profiles)이 원천이고, 이 스크립트가 그대로 넣는다.

기본(--dry-run)은 파일 검증과 계획 출력만 하며 DB에 접속하지 않는다. --check는 명시적인 읽기 전용
대조다. --apply(기존 --execute와 같음)는 없는 판본만 INSERT … ON CONFLICT DO NOTHING으로 넣고 다시
대조한다. 같은 판본 내용이 다르면 덮어쓰지 않으므로 파일의 profile_version을 올린다.

공용 환경을 포함해 DSN은 운영자 터미널의 DATABASE_URL에서만 읽고 출력하지 않는다. 입력은 반드시
synthetic 프로필이며 접속·INSERT 전에 검증한다. 공용 DB 적용은 운영자만 수행한다.
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


def _synthetic(profiles: list[PartnerProfile]) -> None:
    """검증을 우회해 만든 모델도 실제 자료로 시드할 수 없게 한다."""
    if any(p.data_origin != "synthetic" or p.profile_source != "synthetic" for p in profiles):
        raise ValueError("TFT_SEED_SYNTHETIC_ONLY")


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
    _synthetic(profiles)
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
    """기본 무접속 계획, 명시 읽기 대조 또는 명시 적용 뒤 결과를 출력한다.

    Args:
        argv: 명령행 인자. None이면 sys.argv.

    Returns:
        int: dry-run 성공·모두 SAME은 0, 대조 차이 또는 DSN·파일 오류는 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=PROFILES)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="검증·계획만(기본, DB 접속 없음)")
    mode.add_argument("--check", action="store_true", help="DB 읽기 전용 대조")
    mode.add_argument(
        "--apply",
        "--execute",
        dest="apply",
        action="store_true",
        help="없는 합성 판본만 넣고 대조(운영자 전용)",
    )
    args = parser.parse_args(argv)
    try:
        profiles = load_profiles(args.profiles)
        _synthetic(profiles)
    except (OSError, ValueError):
        print("TFT_SEED_FAILED: 합성 프로필 파일을 확인한다.", file=sys.stderr)
        return 1
    if not args.apply and not args.check:
        for profile in profiles:
            print(f"{profile.candidate_code}\tv{profile.profile_version}\tPLANNED")
        print(
            json.dumps({"mode": "dry-run", "profiles": len(profiles), "data_origin": "synthetic"})
        )
        return 0
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("TFT_SEED_NEEDS_DB: DATABASE_URL이 필요하다.", file=sys.stderr)
        return 1
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            if args.check:
                conn.execute("SET TRANSACTION READ ONLY")
            inserted = insert(conn, profiles) if args.apply else 0
            statuses = compare(conn, profiles)
    except (OSError, ValueError, psycopg.Error):
        # DSN·드라이버 예외·원문을 출력하지 않는다.
        print("TFT_SEED_FAILED: 합성 프로필 파일과 DB 연결을 확인한다.", file=sys.stderr)
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
