"""보유시간 차등화: 키워드 기반 hold profile.

버킷별/키워드별 최대 보유시간(분)을 결정.
- 공급계약/수주: 15분 (당일 반전 리스크)
- 특허/임상: 30분 (KOSDAQ 특히)
- 주주환원(소각/취득): EOD까지 (장기 효과)
- 기본: config.max_hold_minutes (30분)
"""

from __future__ import annotations

from kindshot.config import Config

# 키워드 → 보유시간(분). 0 = EOD까지 (max_hold 비활성).
# 긴 키워드가 먼저 매칭되도록 길이 역순 정렬하여 검색.
_HOLD_PROFILES: list[tuple[str, int]] = [
    # 공급계약/수주: 당일 반전 리스크 → 15분
    ("공급계약", 15),
    ("수주", 15),
    ("납품계약", 15),
    ("공급 계약", 15),
    # 특허/임상: 5~30분
    ("FDA", 30),
    ("임상3상", 30),
    ("임상 3상", 30),
    ("특허", 30),
    ("임상2상", 20),
    ("임상 2상", 20),
    ("기술수출", 20),
    # 주주환원: 장기 효과 → EOD
    ("자사주 소각", 0),
    ("자사주소각", 0),
    ("자사주 취득", 0),
    ("자사주취득", 0),
    ("배당", 0),
    ("주주환원", 0),
    # M&A: 중기
    ("합병", 0),
    ("인수", 0),
]


def get_max_hold_minutes(headline: str, keyword_hits: list[str], config: Config) -> int:
    """headline과 keyword_hits를 기반으로 최대 보유시간(분) 반환.

    Returns:
        0이면 max_hold 비활성 (EOD까지), 양수면 해당 분수.
    """
    # keyword_hits 먼저 확인 (bucket 분류에 사용된 키워드)
    for kw, minutes in _HOLD_PROFILES:
        for hit in keyword_hits:
            if kw in hit:
                return minutes

    # headline에서 직접 매칭
    for kw, minutes in _HOLD_PROFILES:
        if kw in headline:
            return minutes

    # 기본값: config
    return config.max_hold_minutes
