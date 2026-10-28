"""가상 협력사 프로필 파일 읽기. KAN-182 저장 구조가 생기면 이 파일을 시드로 쓴다."""

from pathlib import Path

from pydantic import TypeAdapter

from backend.api.tft.partners.schemas import PartnerProfile

PROFILES = Path(__file__).with_name("profiles.json")


def load_profiles(path: Path = PROFILES) -> list[PartnerProfile]:
    """프로필 파일을 읽어 형식과 프로필 안 참조를 검증한다.

    Args:
        path: 프로필 JSON 배열 파일 경로.

    Returns:
        검증된 프로필 목록(파일 순서).

    Raises:
        pydantic.ValidationError: 형식이 공유 형식과 다를 때.
        ValueError: candidate_code나 product_code가 겹치거나, 사양·인증이 자기 products에
            없는 제품코드·모델을 가리킬 때.
    """
    profiles = TypeAdapter(list[PartnerProfile]).validate_json(path.read_bytes())
    codes = [profile.candidate_code for profile in profiles]
    if len(codes) != len(set(codes)):
        raise ValueError("candidate_code가 겹칩니다.")
    product_codes = [item.product_code for profile in profiles for item in profile.products]
    if len(product_codes) != len(set(product_codes)):
        raise ValueError("product_code가 겹칩니다.")
    for profile in profiles:
        own_codes = {item.product_code for item in profile.products}
        own_models = {item.model for item in profile.products}
        if any(fact.product_code not in own_codes for fact in profile.spec_facts):
            raise ValueError(
                f"{profile.candidate_code}: spec_facts가 products에 없는 제품코드를 가리킵니다."
            )
        if any(not set(c.applicable_models) <= own_models for c in profile.certifications):
            raise ValueError(f"{profile.candidate_code}: 인증 적용 모델이 products에 없습니다.")
    return profiles
