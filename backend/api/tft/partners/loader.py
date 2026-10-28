"""가상 협력사 프로필 파일 읽기. KAN-182 저장 구조가 생기면 이 파일을 시드로 쓴다."""

from pathlib import Path

from pydantic import TypeAdapter

from backend.api.tft.partners.schemas import PartnerProfile

PROFILES = Path(__file__).with_name("profiles.json")


def load_profiles(path: Path = PROFILES) -> list[PartnerProfile]:
    """프로필 파일을 읽어 형식을 검증한다.

    Args:
        path: 프로필 JSON 배열 파일 경로.

    Returns:
        검증된 프로필 목록(파일 순서).

    Raises:
        pydantic.ValidationError: 형식이 공유 형식과 다를 때.
        ValueError: candidate_code가 겹칠 때.
    """
    profiles = TypeAdapter(list[PartnerProfile]).validate_json(path.read_bytes())
    codes = [profile.candidate_code for profile in profiles]
    if len(codes) != len(set(codes)):
        raise ValueError("candidate_code가 겹칩니다.")
    return profiles
