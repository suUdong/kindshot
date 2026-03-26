"""보유시간 차등화: 키워드 기반 hold profile.

버킷별/키워드별 최대 보유시간(분)을 결정.
- 공급계약/수주: 15분 (당일 반전 리스크)
- 특허/임상: 30분 (KOSDAQ 특히)
- 주주환원(소각/취득): EOD까지 (장기 효과)
- 인수/합병: 30분 (초기 반응은 있으나 EOD carry는 과도)
- 기본: config.max_hold_minutes (30분)
"""

from __future__ import annotations

from typing import Protocol


class _HasMaxHoldMinutes(Protocol):
    max_hold_minutes: int

# 키워드 → 보유시간(분). 0 = EOD까지 (max_hold 비활성).
# 긴 키워드가 먼저 매칭되도록 길이 역순 정렬하여 검색.
_HOLD_PROFILES: list[tuple[str, int]] = [
    # 공급계약/수주: 20분 (15분은 너무 타이트, 모멘텀 소화 시간 부족)
    ("공급계약", 20),
    ("수주", 20),
    ("납품계약", 20),
    ("공급 계약", 20),
    # 특허/임상: 5~30분
    ("FDA", 30),
    ("품목허가", 30),
    ("임상3상", 30),
    ("임상 3상", 30),
    ("특허", 30),
    ("임상2상", 20),
    ("임상 2상", 20),
    ("기술수출", 20),
    ("기술이전", 20),
    ("CDMO", 20),
    ("라이선스 아웃", 20),
    # 주주환원: 장기 효과 → EOD
    ("자사주 소각", 0),
    ("자사주소각", 0),
    ("자기주식 소각", 0),
    ("자기주식소각", 0),
    ("전량 소각", 0),
    ("전량소각", 0),
    ("소각 결의", 0),
    ("소각결의", 0),
    ("자사주 취득", 0),
    ("자사주취득", 0),
    ("자기주식 취득", 0),
    ("자기주식취득", 0),
    ("자사주 매입", 0),
    ("자사주매입", 0),
    ("자사주 추가 매입", 0),
    ("자사주추가매입", 0),
    ("배당", 0),
    ("주주환원", 0),
    # M&A: 초기 재평가 구간은 보되 EOD carry는 피함
    ("합병", 30),
    ("인수", 30),
    # 공개매수/경영권 분쟁: 장기 효과 → EOD
    ("공개매수", 0),
    ("경영권 분쟁", 0),
    ("경영권분쟁", 0),
    ("위임장 대결", 0),
    # 실적 서프라이즈: 30분 (추가 매수세 유입)
    ("어닝 서프라이즈", 30),
    ("어닝서프라이즈", 30),
    ("사상최대 실적", 30),
    ("사상 최대 실적", 30),
    ("사상최대 영업이익", 30),
    ("사상 최대 영업이익", 30),
    ("역대 최대 실적", 30),
    ("역대 최대 영업이익", 30),
    ("흑자전환", 30),
    ("흑자 전환", 30),
    ("깜짝 실적", 30),
    # 라이선스/기술수출 확정: 20분
    ("라이선스 아웃", 20),
    # 정부/조달 계약: 15분 (안정적이나 단발성 반응)
    ("정부 조달", 15),
    ("조달청", 15),
    # 첫 양산/수주: 20분 (모멘텀 전환 구간)
    ("최초 수주", 20),
    ("첫 수주", 20),
    ("양산 개시", 20),
]


def resolve_hold_profile(
    headline: str,
    keyword_hits: list[str],
    config: _HasMaxHoldMinutes,
) -> tuple[int, str | None]:
    """Resolve the hold profile and the matched keyword, if any."""
    # keyword_hits 먼저 확인 (bucket 분류에 사용된 키워드)
    for kw, minutes in _HOLD_PROFILES:
        for hit in keyword_hits:
            if kw in hit:
                return minutes, kw

    # headline에서 직접 매칭
    for kw, minutes in _HOLD_PROFILES:
        if kw in headline:
            return minutes, kw

    # 기본값: config
    return config.max_hold_minutes, None


def get_max_hold_minutes(headline: str, keyword_hits: list[str], config: _HasMaxHoldMinutes) -> int:
    """headline과 keyword_hits를 기반으로 최대 보유시간(분) 반환.

    Returns:
        0이면 max_hold 비활성 (EOD까지), 양수면 해당 분수.
    """
    minutes, _matched = resolve_hold_profile(headline, keyword_hits, config)
    return minutes
