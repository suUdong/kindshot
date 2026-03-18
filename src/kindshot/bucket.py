"""Keyword-based 6-bucket classification with NEG-first override.

Bucket priority: IGNORE_OVERRIDE > NEG_STRONG > POS_STRONG > NEG_WEAK > POS_WEAK > IGNORE > UNKNOWN
Longer (compound) keywords are matched before shorter ones within each list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from kindshot.models import Bucket


# ── Keyword lists ──────────────────────────────────────

# IGNORE: 트레이딩 시그널 가치 없는 노이즈 (최우선 필터링)
IGNORE_KEYWORDS: list[str] = [
    # 주총/정기 공시
    "정기주주총회", "주주총회", "주총",
    "의결권 행사", "주주명부 폐쇄", "감사위원 분리선출",
    "주주총회소집결의", "주주총회소집공고",
    "주주총회 집중일", "주주명부폐쇄기간",
    # 감사보고서 (적정의견 = 정기 제출)
    "감사보고서 제출", "감사의견 적정",
    "사업보고서 제출", "반기보고서 제출", "분기보고서 제출",
    # 지분 변동 (소량/정기)
    "소유주식수 증가", "소유주식수 감소", "최대주주등 소유주식",
    "특수관계인 지분 변동", "자기주식 신탁",
    # 배당 (기계적/행정)
    "배당락일", "배당락", "배당기준일", "배당 유지",
    # 분할 (방향 불명)
    "분할합병", "존속법인", "신설법인", "사업부문 분리",
    # 거래소/ETP 운영성 notice
    "괴리율 초과 발생",
    "투자유의 안내",
    "소수계좌 거래집중 종목",
    "특정계좌(군) 매매관여 과다종목",
    "단기과열종목",
    "공매도 과열종목 지정",
    # 리포트/요약형 기사 묶음 제목
    "전일 장마감 후 주요 종목 공시",
    "[오늘의 주요공시]",
    "전 거래일(",
    "장중 주요 종목 공시",
    "전일자 주요 지분 변동 공시",
    "[굿모닝증시]",
    # 의무공시 (구조적 보고 — 시그널 없음)
    "매출액 또는 손익구조 30%",
    "매출액 또는 손익구조 15%",
    "매출액또는손익구조30%",
    "매출액또는손익구조15%",
    "수시공시의무관련사항",
    "기타 경영사항(자율공시)",
    "(자회사의 주요경영사항)",
    "투자판단 관련 주요경영사항",
    # IR/기업설명회 (정기 행사)
    "기업설명회(IR) 개최",
    "[IR일정]",
    "[IR]",
    "경영실적 설명",
    # 부동산/리츠 행정
    "위탁관리부동산투자회사",
    "기준일 설정",
    # 건조한 실적 숫자 (방향성 없는 단순 보고)
    "연결 영업이익",
    "개별 영업이익",
    "증권 발행결과",
    # 기업가치 제고 (자율공시, 실행 불확실)
    "기업가치 제고 계획",
    # 배당 행정 (기준일/폐쇄 변형)
    "배당을위한주주명부폐쇄",
    "배당을 위한 주주명부 폐쇄",
    # 인사/조직 (방향 불명)
    "이사회 구성",
    "사명 변경",
]

IGNORE_OVERRIDE_KEYWORDS: list[str] = [
    "신탁계약 해지",
    "신탁 계약 해지",
    "신탁 해지",
    # ETF/ETN 행정 공시 (공급계약·계약체결 등 POS 키워드 오매칭 방지)
    "유동성공급계약",
    "유동성공급자(LP)",
    "지정참가회사(AP)",
    "ETF유동성공급",
]

NEG_STRONG_KEYWORDS: list[str] = [
    # 기존
    "유증", "유상증자",
    "CB발행", "CB 발행", "전환사채",
    "전환가 조정", "전환가조정",
    "대주주 매각", "대주주매각",
    "블록딜",
    "소송 제기", "소송제기",
    "소송 등의 제기", "소송등의제기",
    "소송 개시",
    "피소",
    "패소", "항소심 패소",
    "가처분 신청", "가처분",
    "규제 위반", "규제위반",
    "규제 제재", "규제제재",
    "규제 리스크",
    "규제 강화", "규제강화",
    "공급계약 해지", "공급 계약 해지",
    "계약 해지",
    "철회",
    "취소",
    # 실적 (방향성 명확)
    "어닝 쇼크", "어닝쇼크",
    "적자전환", "적자 전환",
    "영업적자",
    "영업이익 급감",
    "실적 악화",
    "대규모 적자",
    "적자 확대",
    # 감사의견 (비적정)
    "감사의견 거절", "의견거절", "의견 거절",
    "부적정의견", "부적정 의견", "부적정 감사의견", "비적정 감사의견",
    "감사보고서 미제출",
    "계속기업 불확실성", "계속기업 불확실", "계속기업 의문",
    "관리종목 지정",
    "상장폐지 심사", "상장적격성 심사",
    "한정의견", "한정 의견",
    "불성실공시법인지정", "불성실공시법인 지정",
    # 계약/사업 파기·실패 (POS 키워드의 부정적 문맥 방지)
    "특허 만료", "특허만료",
    "특허 침해 소송", "특허침해 소송", "특허 침해",
    "합작 해지", "합작 파기",
    "MOU 파기", "MOU 해지", "MOU 철회",
    "투자유치 실패", "투자 유치 실패",
    "승인 거부", "허가 거부",
    "FDA 승인 거부", "FDA 거부",
    "인수 실패", "인수 포기", "인수 무산",
    "합병 무산", "합병 실패",
    "수주 취소", "수주 철회",
    "흑자전환 실패", "흑자 전환 실패",
    "규모 축소",
    # 분할 (물적분할 = 소액주주 희석)
    "물적분할 후 상장",
    "물적분할 공시", "물적분할 결정",
    "물적분할",
    "분할 상장",
    # 경영권 분쟁 종료 (원점 복귀 패턴) — 복합 키워드 우선
    "경영권 분쟁 종료", "경영권 분쟁 합의", "경영권 분쟁 일단락",
    # 배당
    "무배당 결정", "배당 중단", "배당 미지급",
    # 지분
    "최대주주 지분 대량 매각", "대주주 지분 매각",
    # 바이오/제약 (임상 실패)
    "임상 실패", "임상실패",
    "임상 중단", "임상시험 중단",
    "무용성 평가 실패",
    "CRL 수신", "CRL수신",
    "FDA 허가 불발",
    "보완요구서한",
    "1차 평가변수 미충족",
    "유효성 미입증",
    "임상 중대 이상사례",
]

POS_STRONG_KEYWORDS: list[str] = [
    # 기존
    "수주",
    "공급계약", "공급 계약",
    "실적 상향", "실적상향",
    "자사주 매입", "자사주매입", "자사주 소각", "자사주소각",
    "자기주식 취득", "자기주식취득", "자기주식 소각", "자기주식소각",
    "신규사업", "신규 사업",
    "합작",
    "대형 계약", "대형계약",
    "인수",
    "지분 취득", "지분취득",
    "특허",
    "허가 획득", "품목허가 승인", "식약처 허가",
    "매출 확대", "매출확대",
    "투자유치",
    "MOU", "업무협약",
    # 실적 (방향성 명확)
    "어닝 서프라이즈", "어닝서프라이즈",
    "사상최대 실적", "사상최대 영업이익", "사상최대 매출",
    "사상 최대 실적", "사상 최대 영업이익", "사상 최대 매출",
    "창사 이래 최고", "창사 이래 최대",
    "최고 실적", "최대 실적", "최대 영업이익", "최대 매출",
    "역대 최대 실적", "역대 최대 영업이익", "역대 최대 매출",
    "역대 최고 실적", "역대 최고 영업이익", "역대 최고 매출",
    "역대 최대 달성", "역대 최대",
    "매출 최고치", "최고치 경신",
    "사상 최대",  # NEG_STRONG 우선이므로 "사상 최대 적자"는 NEG로 잡힘
    "최고 경신",
    "흑자전환", "흑자 전환",
    "영업이익 급증",
    "실적 호전",
    "깜짝 실적",
    # 배당/주주환원 (규모 확대)
    "특별배당", "특별 배당",
    "깜짝 배당",
    "배당 대폭 증가", "배당 대폭 확대",
    "주주환원 확대", "주주환원 강화",
    "주주환원 추진",
    # 경영권 분쟁 (초기 = 주가 급등)
    "경영권 분쟁", "경영권분쟁",
    "위임장 대결",
    "공개매수", "대항 공개매수",
    "적대적 인수", "적대적 M&A",
    "지분 확보 경쟁",
    "경영권 승계 분쟁",
    # 바이오/제약
    "임상 3상 성공",
    "FDA 승인", "FDA 허가",
    "신약 허가",
    "기술수출 계약 체결", "기술수출 계약",
    "기술이전 계약",
    "라이선스 아웃",
    "시판허가", "시판 허가",
    "NDA 승인",
    "BLA 승인",
    "패스트트랙 지정",
    "블록버스터 계약",
    "임상 2상 완료",
    "임상 2상 승인",
    # 주식 소각 (자기주식소각 외 변형)
    "주식 소각 결정",
    # 글로벌 확장 / 전략적
    "글로벌 공급망 확대",
    "글로벌 사업 본격화",
    "글로벌 시장 진출",
    "분할재상장 예비심사 통과",
    "기술수출",
    # 계약 유형 (공급계약 외)
    "CMO 계약", "CDMO 계약", "위탁생산계약",
    "설계 계약", "개발 계약",
    "제휴 계약 체결", "사업 제휴 계약", "전략적 제휴",
    "제휴계약 체결", "사업제휴 계약",
    # 실적 성장 (구체적 표현만)
    "영업이익 성장", "매출 성장",
    "분기 최대", "분기 최고",
    "영업이익 400%", "영업이익 300%", "영업이익 200%",
    "매출 400%", "매출 300%", "매출 200%",
]

NEG_WEAK_KEYWORDS: list[str] = [
    # 기존
    "루머",
    "풍문",
    "목표가 하향",
    # 실적
    "매출 감소",
    "순이익 감소",
    "실적 부진",
    "매출 급감",
    "컨센서스 하회",
    "실적 둔화",
    # 감사
    "감사의견 변경",
    # 분할
    "주식매수청구권",
    # 배당
    "배당 감소", "배당 축소",
    # 지분
    "최대주주 변경", "최대주주변경",
    "내부자 매도",
    "자기주식 처분",
    # 바이오
    "임상 지연",
    "FDA 심사기간 연장", "FDA 심사 기간 연장",
    "기술이전 계약 해지",
    "파트너십 우선순위 하향",
    # 매출/실적 부진
    "매출액 미달", "매출 미달",
    "영업익 감소",
    # 자사주 처분 (매각 = 희석)
    "자사주 처분", "자기주식 처분 결정",
]

POS_WEAK_KEYWORDS: list[str] = [
    # 기존
    "리포트",
    "전망",
    "테마",
    "목표가",
    "재평가",
    "저평가",
    "소송 승소", "항소심 승소", "2심서 승소", "승소 판결", "대법 승소",
    "현금배당",
    "현금 배당",
    "현금ㆍ현물배당",
    "현금ㆍ현물 배당",
    # 실적
    "매출 증가",
    "영업이익 증가",
    "순이익 증가",
    "실적 개선",
    "실적 호조",
    "매출 급증",
    "컨센서스 상회",
    "실적 성장",
    "매출 성장",
    "흑자전환 기대", "흑자 전환 기대",
    "실적 개선 기대",
    "성장 기대",
    "매출 상승",
    # 분할
    "인적분할", "인적분할 결정", "인적분할 공시",
    # 경영권/주주행동
    "행동주의 펀드", "행동주의 주주",
    "주주제안",
    "임시주주총회 소집",
    "경영권 방어",
    "소수주주권 행사",
    "배당 확대 요구",
    "자사주 소각 요구",
    # 배당
    "현금배당 결정",
    "배당 증가", "배당 확대",
    "중간배당 결정", "중간배당",
    "분기배당 결정", "분기배당",
    "배당수익률",
    # 지분
    "자기주식 취득 결정",
    "자사주 취득",
    "내부자 매수",
    "경영진 자사주", "대표이사 자사주",
    "경영권 안정화",
    # 바이오
    "전임상 결과 긍정적",
    "임상 1상 완료",
    "임상 2상 개시",
    "IND 승인",
    "마일스톤 수령",
    "희귀의약품 지정",
    "임상시험계획 승인",
    "바이오시밀러",
    # 증권사 목표주가/투자의견 (약한 신호)
    "목표주가", "투자의견",
    "최선호주",
    "수혜 기대", "수혜주",
    # 전략적
    "전략적 제휴", "전략적 투자",
    "파트너십",
    "글로벌 확장", "글로벌 진출",
    "수출 승인",
    # 주식분할 (유동성 확대 의도)
    "주식분할결정", "주식분할 결정", "주식 분할 결정",
    # 용역/계약 (소규모)
    "용역계약 체결",
    # 실적 반등/성장
    "실적 반등", "매출 반등",
    "매출 기록", "매출 달성",
    "해외매출", "해외 매출",
]


@dataclass
class BucketResult:
    bucket: Bucket
    keyword_hits: list[str] = field(default_factory=list)
    matched_positions: list[tuple[str, int]] = field(default_factory=list)


def _find_keywords(text: str, keywords: list[str]) -> list[tuple[str, int]]:
    """Find all keyword matches with their positions."""
    matches: list[tuple[str, int]] = []
    for kw in keywords:
        idx = text.find(kw)
        if idx >= 0:
            matches.append((kw, idx))
    return matches


def classify(headline: str) -> BucketResult:
    """Classify headline into one of 6 buckets.

    Priority: IGNORE_OVERRIDE > NEG_STRONG > POS_STRONG > NEG_WEAK > POS_WEAK > IGNORE > UNKNOWN
    """
    text = headline

    ignore_override = _find_keywords(text, IGNORE_OVERRIDE_KEYWORDS)
    if ignore_override:
        return BucketResult(
            bucket=Bucket.IGNORE,
            keyword_hits=[kw for kw, _ in ignore_override],
            matched_positions=ignore_override,
        )

    # Priority 1: NEG_STRONG
    neg_strong = _find_keywords(text, NEG_STRONG_KEYWORDS)
    if neg_strong:
        return BucketResult(
            bucket=Bucket.NEG_STRONG,
            keyword_hits=[kw for kw, _ in neg_strong],
            matched_positions=neg_strong,
        )

    # Priority 2: POS_STRONG
    pos_strong = _find_keywords(text, POS_STRONG_KEYWORDS)
    if pos_strong:
        return BucketResult(
            bucket=Bucket.POS_STRONG,
            keyword_hits=[kw for kw, _ in pos_strong],
            matched_positions=pos_strong,
        )

    # Priority 3: NEG_WEAK
    neg_weak = _find_keywords(text, NEG_WEAK_KEYWORDS)
    if neg_weak:
        return BucketResult(
            bucket=Bucket.NEG_WEAK,
            keyword_hits=[kw for kw, _ in neg_weak],
            matched_positions=neg_weak,
        )

    # Priority 4: POS_WEAK
    pos_weak = _find_keywords(text, POS_WEAK_KEYWORDS)
    if pos_weak:
        return BucketResult(
            bucket=Bucket.POS_WEAK,
            keyword_hits=[kw for kw, _ in pos_weak],
            matched_positions=pos_weak,
        )

    # Priority 5: IGNORE (노이즈 — NEG/POS에 안 걸린 것만)
    ignore = _find_keywords(text, IGNORE_KEYWORDS)
    if ignore:
        return BucketResult(
            bucket=Bucket.IGNORE,
            keyword_hits=[kw for kw, _ in ignore],
            matched_positions=ignore,
        )

    # Priority 6: UNKNOWN
    return BucketResult(bucket=Bucket.UNKNOWN)
