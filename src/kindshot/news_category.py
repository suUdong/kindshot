"""뉴스 카테고리 분류 및 카테고리별 confidence 가중치.

v67: 뉴스 유형별 성과 분석 기반 confidence 보정.
카테고리별 과거 승률/P&L 데이터를 반영하여 진입 판단 정밀도 향상.
"""

from __future__ import annotations

# 뉴스 유형 분류 규칙 (backtest_analysis.py NEWS_TYPE_RULES와 동기화)
# 순서가 우선순위: 먼저 매칭되는 카테고리가 적용됨
NEWS_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("shareholder_return", (
        "자사주 소각", "자사주소각", "자사주 취득", "자사주취득",
        "자사주 매입", "자사주매입", "배당", "주주환원", "공개매수",
    )),
    ("mna", (
        "인수", "합병", "M&A", "경영권 분쟁", "경영권분쟁", "위임장 대결",
    )),
    ("clinical_regulatory", (
        "FDA", "품목허가", "임상3상", "임상 3상", "임상2상", "임상 2상",
        "승인", "허가", "특허", "AACR",
    )),
    ("contract", (
        "공급계약", "공급 계약", "수주", "납품계약", "독점 공급",
        "조달청", "정부 조달", "양산 개시", "첫 수주", "최초 수주",
    )),
    ("earnings_turnaround", (
        "실적", "흑자전환", "흑자 전환", "어닝",
        "사상 최대", "사상최대", "역대 최대", "역대최대",
    )),
    ("product_technology", (
        "개발", "출시", "론칭", "기술이전", "기술수출",
        "라이선스 아웃", "CDMO", "플랫폼", "신제품",
    )),
    ("policy_funding", (
        "국책", "정책", "지원", "업무협약", "MOU",
        "투자유치", "보조금", "수주잔고",
    )),
]


def classify_news_type(headline: str, keyword_hits: list[str] | None = None) -> str:
    """헤드라인 + 키워드 히트로 뉴스 카테고리 분류.

    Returns:
        카테고리 문자열 (e.g. "contract", "mna") 또는 "other"
    """
    search_text = headline
    if keyword_hits:
        search_text = headline + " " + " ".join(keyword_hits)

    for category, keywords in NEWS_TYPE_RULES:
        for kw in keywords:
            if kw in search_text:
                return category
    return "other"


# v67 카테고리별 confidence 보정값
# 양수 = 부스트, 음수 = 페널티
# 근거: backtest 9일간 카테고리별 승률/P&L 분석 + 도메인 지식
#   - shareholder_return: 장기 트렌드, EOD hold → 높은 승률 기대
#   - clinical_regulatory: FDA/임상 촉매 강력 but 변동성 큼
#   - contract: 수주/공급계약 — 가장 빈번, 중간 승률
#   - earnings_turnaround: 실적 → 이미 반영된 경우 많아 보수적
#   - mna: M&A — 강력 but 루머 리스크
#   - product_technology: 개발/출시 — 모멘텀 약함
#   - policy_funding: 정책/MOU — 모멘텀 약함, 단기 효과 미미
CATEGORY_CONFIDENCE_ADJUSTMENTS: dict[str, int] = {
    "shareholder_return": 2,     # v71: 3→2 (자사주매입 -0.528%, 실전 데이터 반영)
    "clinical_regulatory": 0,    # v71: 2→0 (실전 25%승률, -0.646% — 대형 바이오 손실)
    "mna": 5,                    # v71: 1→5 (실전 100%승률, 유일한 수익원 — 강력 부스트)
    "contract": -2,              # v72: -5→-2 (금호건설 공급계약 TP+2.2% — 공시 자체는 유효, 시간대 가드레일이 09시 손실 차단)
    "earnings_turnaround": -1,   # v72: -3→-1 (삼성증권 close+0.94%, 흑자전환 중립 — 과도한 페널티 완화)
    "product_technology": -2,    # 개발/출시: 단기 모멘텀 약함
    "policy_funding": -2,        # 정책/MOU: 단기 효과 미미
    "other": 2,                  # v71: 0→2 (실전 other_positive 100%승률 +0.314%)
}


def get_category_confidence_adjustment(category: str) -> int:
    """카테고리별 confidence 보정값 반환."""
    return CATEGORY_CONFIDENCE_ADJUSTMENTS.get(category, 0)
