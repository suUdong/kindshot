"""Tests for keyword bucketing with NEG-first override."""

import pytest

from kindshot.bucket import classify
from kindshot.models import Bucket


def test_pos_strong_supply_contract():
    result = classify("삼성전자, 신규 공급계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "공급계약" in result.keyword_hits


def test_pos_strong_supply_contract_with_space():
    result = classify("비츠로시스 자회사, 대규모 공급 계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "공급 계약" in result.keyword_hits


def test_neg_strong_override():
    """NEG keyword overrides POS keyword."""
    result = classify("A사, 공급계약 해지 결정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "공급계약 해지" in result.keyword_hits


def test_neg_strong_cb():
    result = classify("전환사채(CB) 발행 결정")
    assert result.bucket == Bucket.NEG_STRONG


def test_pos_strong_buyback():
    # "취득" != "취소" so this should be POS_STRONG
    result = classify("자사주 매입 결정")
    assert result.bucket == Bucket.POS_STRONG


def test_pos_strong_approval_signal():
    result = classify("종근당, 당뇨병 치료제 품목허가 승인")
    assert result.bucket == Bucket.POS_STRONG
    assert "품목허가 승인" in result.keyword_hits


def test_pos_strong_mfds_approval_signal():
    result = classify("라메디텍, 채혈·혈당 측정기기 식약처 허가")
    assert result.bucket == Bucket.POS_STRONG
    assert "식약처 허가" in result.keyword_hits


def test_ignore_shareholder_meeting():
    result = classify("주주총회 소집 결과")
    assert result.bucket == Bucket.IGNORE

def test_ignore_audit_report():
    result = classify("삼성전자(주) 감사보고서 제출")
    assert result.bucket == Bucket.IGNORE

def test_ignore_share_count_change():
    result = classify("케이뱅크, 최대주주등 소유주식수 126,690,193주 증가")
    assert result.bucket == Bucket.IGNORE

def test_unknown_no_keywords():
    result = classify("로킷헬스케어 탈모치료 소재 전임상서 모낭 수 늘고 모발 굵어져")
    assert result.bucket == Bucket.UNKNOWN


def test_pos_weak_cash_dividend_decision():
    result = classify("미창석유, 주당 3,500원 현금배당 결정")
    assert result.bucket == Bucket.POS_WEAK
    assert "현금배당" in result.keyword_hits


def test_pos_weak_target_price_revision():
    result = classify("KCC, 저평가 해소 기대…목표가 68만원 상향")
    assert result.bucket == Bucket.POS_WEAK
    assert "목표가" in result.keyword_hits


def test_neg_weak_target_price_cut_overrides_positive_target_word():
    result = classify("KCC, 실적 둔화 우려…목표가 하향")
    assert result.bucket == Bucket.NEG_WEAK
    assert "목표가 하향" in result.keyword_hits


def test_matched_positions_logged():
    result = classify("대형 수주 및 공급계약 체결")
    assert len(result.keyword_hits) >= 2
    assert len(result.matched_positions) >= 2


def test_withdrawal_still_neg():
    result = classify("정정(취소) 유상증자 결정")
    assert result.bucket == Bucket.NEG_STRONG


def test_ignore_buyback_trust_contract_termination():
    result = classify("하이트진로홀딩스, 15억원 규모 자사주 취득 신탁계약 해지")
    assert result.bucket == Bucket.IGNORE
    assert "신탁계약 해지" in result.keyword_hits


def test_ignore_trust_termination_without_contract_word():
    result = classify("실리콘투, 자기주식 신탁 해지 결정")
    assert result.bucket == Bucket.IGNORE
    assert "신탁 해지" in result.keyword_hits


def test_pos_weak_regulatory_easing_not_negative():
    result = classify("셀트리온, 바이오시밀러 글로벌 규제 완화로 최대 수혜 전망")
    assert result.bucket == Bucket.POS_WEAK


def test_neg_strong_regulatory_sanction_phrase():
    result = classify("A사, 금융당국 규제 제재로 신규 영업 차질 우려")
    assert result.bucket == Bucket.NEG_STRONG
    assert "규제 제재" in result.keyword_hits


def test_neg_strong_regulatory_violation_phrase():
    result = classify("A사, 중대 규제 위반 적발")
    assert result.bucket == Bucket.NEG_STRONG
    assert "규제 위반" in result.keyword_hits


def test_unknown_generic_lawsuit_not_negative():
    result = classify("삼성전자서비스 퇴직자도 \"퇴직금 더 줘\"...줄소송 현실화")
    assert result.bucket == Bucket.UNKNOWN


def test_unknown_generic_lawsuit_disclosure_title():
    result = classify("(주)세아제강 소송등의판결ㆍ결정(자율공시:일정금액미만의청구)")
    assert result.bucket == Bucket.UNKNOWN


def test_neg_strong_lawsuit_filing_phrase():
    result = classify("(주)원일티엔아이 소송등의제기ㆍ신청(일정금액 이상의 청구)")
    assert result.bucket == Bucket.NEG_STRONG
    assert "소송등의제기" in result.keyword_hits


def test_neg_strong_lawsuit_loss_phrase():
    result = classify("엔씨소프트, '아키에이지 워' 저작권 소송 항소심 패소")
    assert result.bucket == Bucket.NEG_STRONG
    assert "항소심 패소" in result.keyword_hits


def test_pos_weak_lawsuit_win_phrase():
    result = classify("LX하우시스, 단열재 특허 무효소송 2심서 승소")
    assert result.bucket == Bucket.POS_STRONG
    assert "특허" in result.keyword_hits


# ── New bucket tests ──────────────────────────────────

def test_neg_strong_earnings_shock():
    result = classify("A사, 3분기 어닝 쇼크…영업적자 전환")
    assert result.bucket == Bucket.NEG_STRONG
    assert "어닝 쇼크" in result.keyword_hits

def test_pos_strong_earnings_surprise():
    result = classify("삼성전자, 어닝 서프라이즈…사상최대 영업이익")
    assert result.bucket == Bucket.POS_STRONG
    assert "어닝 서프라이즈" in result.keyword_hits

def test_neg_strong_spin_off():
    result = classify("LG화학, 배터리사업부 물적분할 결정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "물적분할 결정" in result.keyword_hits

def test_pos_weak_demerger():
    result = classify("현대백화점, 면세사업부 인적분할 결정")
    assert result.bucket == Bucket.POS_WEAK
    assert "인적분할 결정" in result.keyword_hits

def test_neg_strong_audit_opinion():
    result = classify("A사, 감사의견 거절로 관리종목 지정")
    assert result.bucket == Bucket.NEG_STRONG

def test_pos_strong_fda():
    result = classify("한미약품, 신약 FDA 승인 획득")
    assert result.bucket == Bucket.POS_STRONG
    assert "FDA 승인" in result.keyword_hits

def test_neg_strong_clinical_failure():
    result = classify("브릿지바이오, 임상 3상 실패…임상 중단")
    assert result.bucket == Bucket.NEG_STRONG

def test_pos_strong_tech_export():
    result = classify("에이비엘바이오, 글로벌 빅파마와 기술수출 계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "기술수출 계약 체결" in result.keyword_hits

def test_pos_strong_proxy_fight():
    result = classify("행동주의 펀드, A사 경영권 분쟁 돌입…공개매수 선언")
    assert result.bucket == Bucket.POS_STRONG
    assert "경영권 분쟁" in result.keyword_hits

def test_neg_strong_proxy_fight_end():
    """경영권 분쟁 종료 = NEG (원점 복귀 패턴), 복합 키워드 우선"""
    result = classify("A사, 경영권 분쟁 종료…양측 합의")
    assert result.bucket == Bucket.NEG_STRONG
    assert "경영권 분쟁 종료" in result.keyword_hits

def test_pos_strong_special_dividend():
    result = classify("삼성전자, 특별배당 1주당 1000원 결정")
    assert result.bucket == Bucket.POS_STRONG
    assert "특별배당" in result.keyword_hits

def test_neg_strong_going_concern():
    result = classify("A사, 계속기업 불확실성 감사보고서 제출")
    assert result.bucket == Bucket.NEG_STRONG
    assert "계속기업 불확실성" in result.keyword_hits


def test_neg_strong_unfaithful_disclosure_designation_no_space():
    result = classify("디와이피(주) 불성실공시법인지정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "불성실공시법인지정" in result.keyword_hits


def test_neg_strong_unfaithful_disclosure_designation_with_space():
    result = classify("DYP, 공시불이행으로 불성실공시법인 지정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "불성실공시법인 지정" in result.keyword_hits


def test_neg_weak_major_shareholder_change_no_space():
    result = classify("주식회사 셀레믹스 최대주주변경")
    assert result.bucket == Bucket.NEG_WEAK
    assert "최대주주변경" in result.keyword_hits


def test_major_shareholder_change_variant_neg_weak():
    """'최대주주가 X로 변경' 패턴도 NEG_WEAK로 분류."""
    result = classify("셀레믹스, 최대주주가 박종갑 외 1인으로 변경")
    assert result.bucket == Bucket.NEG_WEAK
    assert "최대주주가" in result.keyword_hits


def test_ignore_etf_disparity_notice():
    result = classify("삼성 KODEX 경기소비재증권상장지수투자신탁[주식형] ETF 괴리율 초과 발생")
    assert result.bucket == Bucket.IGNORE
    assert "괴리율 초과 발생" in result.keyword_hits


def test_ignore_etn_investor_notice():
    result = classify("미래에셋증권(주) 상장지수증권 투자유의 안내(미래에셋 인버스 2X 코스피200 선물 ETN)")
    assert result.bucket == Bucket.IGNORE
    assert "투자유의 안내" in result.keyword_hits


def test_ignore_account_concentration_notice():
    result = classify("(주)코아스 [투자주의]소수계좌 거래집중 종목")
    assert result.bucket == Bucket.IGNORE
    assert "소수계좌 거래집중 종목" in result.keyword_hits


def test_ignore_overheated_issue_notice():
    result = classify("코오롱글로벌(주) 단기과열종목(가격괴리율, 3거래일 단일가매매) 지정 연장(코오롱글로벌우)")
    assert result.bucket == Bucket.IGNORE
    assert "단기과열종목" in result.keyword_hits


def test_ignore_short_sale_overheat_notice():
    result = classify("서울식품공업(주) 공매도 과열종목 지정(공매도 거래 금지 적용)")
    assert result.bucket == Bucket.IGNORE
    assert "공매도 과열종목 지정" in result.keyword_hits


def test_existing_positive_supply_contract_still_pos_strong():
    result = classify("파두, 226억원 규모 SSD 컨트롤러 공급 계약 체결")
    assert result.bucket == Bucket.POS_STRONG


def test_ignore_post_close_summary_headline():
    result = classify("전일 장마감 후 주요 종목 공시")
    assert result.bucket == Bucket.IGNORE
    assert "전일 장마감 후 주요 종목 공시" in result.keyword_hits


def test_ignore_today_key_disclosures_summary():
    result = classify("[오늘의 주요공시] 인포바인·강원랜드 등")
    assert result.bucket == Bucket.IGNORE
    assert "[오늘의 주요공시]" in result.keyword_hits


def test_ignore_previous_trading_day_summary_format():
    result = classify("[코스피코스닥 전 거래일(12일) 주요공시]")
    assert result.bucket == Bucket.IGNORE
    assert "전 거래일(" in result.keyword_hits


def test_generic_major_disclosure_phrase_stays_unknown():
    result = classify("인포바인 주요공시 관련 해설")
    assert result.bucket == Bucket.UNKNOWN


def test_ignore_intraday_report_list_title():
    result = classify("장중 주요 종목 공시")
    assert result.bucket == Bucket.IGNORE
    assert "장중 주요 종목 공시" in result.keyword_hits


def test_generic_major_stock_disclosure_phrase_stays_unknown():
    result = classify("오늘 주요 종목 공시 해설")
    assert result.bucket == Bucket.UNKNOWN


def test_ignore_previous_day_ownership_change_summary_title():
    result = classify("전일자 주요 지분 변동 공시")
    assert result.bucket == Bucket.IGNORE
    assert "전일자 주요 지분 변동 공시" in result.keyword_hits


def test_generic_ownership_change_summary_phrase_stays_unknown():
    result = classify("오늘 지분 변동 공시 해설")
    assert result.bucket == Bucket.UNKNOWN


def test_ignore_good_morning_market_wrap_prefix():
    result = classify('[굿모닝증시]"중동 포화 속 파월의 입·마이크론 실적…코스피 안개속 장세"')
    assert result.bucket == Bucket.IGNORE
    assert "[굿모닝증시]" in result.keyword_hits


def test_generic_market_wrap_near_match_stays_unknown():
    result = classify("굿모닝 증시 체크 포인트")
    assert result.bucket == Bucket.UNKNOWN


# ── False positive prevention (수익성 보호) ──────────────────

def test_neg_patent_expiry_not_pos():
    """특허 만료는 POS_STRONG '특허'가 아닌 NEG_STRONG으로."""
    result = classify("삼성전자, 바이오시밀러 특허 만료 앞두고 대비책 마련")
    assert result.bucket == Bucket.NEG_STRONG
    assert "특허 만료" in result.keyword_hits


def test_neg_merger_failure_not_pos():
    """인수 합병 무산은 NEG_STRONG."""
    result = classify("현대차, 인수 합병 무산 공식 발표")
    assert result.bucket == Bucket.NEG_STRONG
    assert "합병 무산" in result.keyword_hits


def test_neg_investment_failure_not_pos():
    """투자유치 실패는 NEG_STRONG."""
    result = classify("A사, 투자유치 실패로 자금난")
    assert result.bucket == Bucket.NEG_STRONG
    assert "투자유치 실패" in result.keyword_hits


def test_neg_turnaround_failure_not_pos():
    """흑자전환 실패는 NEG_STRONG."""
    result = classify("B사, 흑자전환 실패…적자 지속")
    assert result.bucket == Bucket.NEG_STRONG
    assert "흑자전환 실패" in result.keyword_hits


def test_neg_fda_rejection_not_pos():
    """FDA 승인 거부는 NEG_STRONG."""
    result = classify("한미약품, FDA 승인 거부 통보")
    assert result.bucket == Bucket.NEG_STRONG
    assert "FDA 승인 거부" in result.keyword_hits or "승인 거부" in result.keyword_hits


def test_neg_mou_cancellation_not_pos():
    """MOU 파기는 NEG_STRONG."""
    result = classify("C사, 전략적 MOU 파기 결정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "MOU 파기" in result.keyword_hits


def test_neg_patent_infringement_not_pos():
    """특허 침해 소송은 NEG_STRONG."""
    result = classify("D사, 특허 침해 소송 제기당해")
    assert result.bucket == Bucket.NEG_STRONG
    assert "특허 침해 소송" in result.keyword_hits or "특허 침해" in result.keyword_hits


def test_neg_scale_reduction_not_pos():
    """규모 축소는 NEG_STRONG."""
    result = classify("E사, 공급계약 규모 축소 통보")
    assert result.bucket == Bucket.NEG_STRONG
    assert "규모 축소" in result.keyword_hits


def test_neg_weak_treasury_disposal():
    """자사주 처분은 NEG_WEAK."""
    result = classify("F사, 자사주 처분 결정")
    assert result.bucket == Bucket.NEG_WEAK
    assert "자사주 처분" in result.keyword_hits


def test_pos_strong_still_works_with_neg_guards():
    """부정 가드 추가 후 정상 POS_STRONG 작동 확인."""
    assert classify("삼성전자, 신규 특허 10건 등록").bucket == Bucket.POS_STRONG
    assert classify("현대차, 대형 수주 계약 체결").bucket == Bucket.POS_STRONG
    assert classify("카카오, 투자유치 성공 발표").bucket == Bucket.POS_STRONG
    assert classify("한미약품, FDA 승인 획득").bucket == Bucket.POS_STRONG
    assert classify("A사, 흑자전환 달성").bucket == Bucket.POS_STRONG
    assert classify("B사, 합작 법인 설립").bucket == Bucket.POS_STRONG


# ── IGNORE 확장 테스트 ──────────────────

def test_ignore_mandatory_disclosure():
    """의무공시 (30% 변동)는 IGNORE."""
    result = classify("비나텍주식회사 매출액 또는 손익구조 30% 이상 변동")
    assert result.bucket == Bucket.IGNORE


def test_ignore_ir_event():
    """기업설명회(IR)은 IGNORE."""
    result = classify("한국자산신탁 기업설명회(IR) 개최")
    assert result.bucket == Bucket.IGNORE


def test_ignore_dry_earnings():
    """건조한 실적 숫자는 IGNORE."""
    result = classify("비나텍, 25년 연결 영업이익 18.05억원")
    assert result.bucket == Bucket.IGNORE


# ── POS_STRONG 확장 테스트 ──────────────────

def test_pos_strong_record_high():
    """'사상 최대'는 POS_STRONG."""
    result = classify("LS, 매출 45조원 '사상 최대'")
    assert result.bucket == Bucket.POS_STRONG
    assert "사상 최대" in result.keyword_hits


def test_pos_strong_shareholder_return():
    """주주환원 확대는 POS_STRONG."""
    result = classify("삼성전자, 주주환원 확대 발표")
    assert result.bucket == Bucket.POS_STRONG
    assert "주주환원 확대" in result.keyword_hits


def test_ignore_override_etf_lp_contract():
    """ETF LP 유동성공급계약은 IGNORE (POS '공급계약' 오매칭 방지)."""
    result = classify("삼성 KODEX ETF유동성공급자(LP)와유동성공급계약의체결")
    assert result.bucket == Bucket.IGNORE


def test_ignore_override_etf_ap_change():
    """ETF AP 지정참가회사 변경은 IGNORE."""
    result = classify("한화 PLUS ETF지정참가회사(AP)추가ㆍ해지ㆍ변경안내")
    assert result.bucket == Bucket.IGNORE


# ── v3 키워드 추가 테스트 ──────────────────

def test_ignore_provisional_earnings_disclosure():
    """잠정실적 공정공시는 IGNORE."""
    result = classify("(주)오리온 연결재무제표기준영업(잠정)실적(공정공시)")
    assert result.bucket == Bucket.IGNORE

def test_ignore_consolidated_earnings_prefix():
    """연결재무제표기준 실적은 IGNORE."""
    result = classify("한국가스공사(주) 연결재무제표기준영업(잠정)실적(공정공시)")
    assert result.bucket == Bucket.IGNORE

def test_neg_strong_delisting():
    """상장폐지는 NEG_STRONG."""
    result = classify("대동전자(주) 상장폐지")
    assert result.bucket == Bucket.NEG_STRONG
    assert "상장폐지" in result.keyword_hits

def test_neg_strong_delisting_does_not_override_existing():
    """상장폐지 심사도 여전히 NEG_STRONG."""
    result = classify("A사, 상장폐지 심사 개시")
    assert result.bucket == Bucket.NEG_STRONG

def test_neg_weak_restructuring():
    """구조조정은 NEG_WEAK."""
    result = classify("토박스코리아, 지난해 매출 397억...고강도 구조조정 추진")
    assert result.bucket == Bucket.NEG_WEAK
    assert "구조조정" in result.keyword_hits

def test_pos_weak_new_facility_investment():
    """신규시설투자는 POS_WEAK."""
    result = classify("팬오션(주) 신규시설투자등(자율공시)")
    assert result.bucket == Bucket.POS_WEAK
    assert "신규시설투자" in result.keyword_hits

def test_pos_weak_subsidiary_stock_acquisition():
    """타법인주식및출자증권취득은 POS_WEAK."""
    result = classify("JW홀딩스(주) 타법인주식및출자증권취득결정")
    assert result.bucket == Bucket.POS_WEAK
    assert "타법인주식및출자증권취득" in result.keyword_hits

def test_pos_weak_clinical_start():
    """임상 착수는 POS_WEAK."""
    result = classify("케어젠, 펩타이드 기반 모공 개선 마스크팩 임상 착수")
    assert result.bucket == Bucket.POS_WEAK
    assert "임상 착수" in result.keyword_hits

def test_pos_weak_shareholder_return_standalone():
    """주주환원 단독 사용도 POS_WEAK."""
    result = classify("주주환원 나서는 영원무역홀딩스… 주당 배당금 23% 증가")
    assert result.bucket == Bucket.POS_WEAK
    assert "주주환원" in result.keyword_hits

def test_pos_strong_shareholder_return_expansion_still_strong():
    """주주환원 확대는 여전히 POS_STRONG (POS_STRONG 우선)."""
    result = classify("삼성전자, 주주환원 확대 발표")
    assert result.bucket == Bucket.POS_STRONG

def test_pos_weak_profitability_achieved():
    """흑자 달성은 POS_WEAK."""
    result = classify("매출 39% 성장·흑자 달성 알로이스")
    assert result.bucket == Bucket.POS_WEAK
    assert "흑자 달성" in result.keyword_hits

def test_pos_weak_new_product_launch():
    """신제품 출시는 POS_WEAK."""
    result = classify("A사, 차세대 반도체 공정 신제품 출시")
    assert result.bucket == Bucket.POS_WEAK
    assert "신제품 출시" in result.keyword_hits


# ── 체결/해지 구분 테스트 ──────────────────

def test_neg_supply_contract_termination():
    """공급계약 해지 → NEG_STRONG (POS '공급계약'보다 우선)."""
    result = classify("삼성전자, 1000억 규모 공급계약 해지 통보")
    assert result.bucket == Bucket.NEG_STRONG
    assert "공급계약 해지" in result.keyword_hits


def test_neg_supply_contract_cancellation():
    """공급계약 파기 → NEG_STRONG."""
    result = classify("현대차, 500억 공급계약 파기")
    assert result.bucket == Bucket.NEG_STRONG
    assert "공급계약 파기" in result.keyword_hits


def test_neg_delivery_contract_termination():
    """납품계약 해지 → NEG_STRONG."""
    result = classify("A사, 납품계약 해지 결정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "납품계약 해지" in result.keyword_hits


def test_neg_order_contract_termination():
    """수주계약 해지 → NEG_STRONG."""
    result = classify("B사, 수주계약 해지 공시")
    assert result.bucket == Bucket.NEG_STRONG
    assert "수주계약 해지" in result.keyword_hits


def test_pos_supply_contract_conclusion():
    """공급계약 체결 → POS_STRONG (해지가 아닌 체결)."""
    result = classify("삼성전자, 5000억 규모 공급계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "공급계약" in result.keyword_hits


def test_neg_contract_release():
    """계약 해제 → NEG_STRONG."""
    result = classify("C사, 주요 거래처 계약 해제 공시")
    assert result.bucket == Bucket.NEG_STRONG
    assert "계약 해제" in result.keyword_hits


# ── 기사 노이즈 IGNORE 테스트 ──────────────────

def test_ignore_card_news_bracket():
    """[종합] 기사 → IGNORE."""
    result = classify("[종합] 삼성SDS 자사주 매입·소각, 상황 맞춰 추진…주주가치 제고")
    assert result.bucket == Bucket.IGNORE
    assert "[종합]" in result.keyword_hits


def test_ignore_tops_pick():
    """[TOP's Pick] 기사 → IGNORE."""
    result = classify("[TOP's Pick] 엔씨소프트, 저스트플레이 인수로 웹보드 시장 진출")
    assert result.bucket == Bucket.IGNORE
    assert "[TOP's Pick]" in result.keyword_hits


def test_ignore_market_insight():
    """[마켓인사이트] 기사 → IGNORE."""
    result = classify("[마켓인사이트] A사, 대규모 투자유치 임박 관측")
    assert result.bucket == Bucket.IGNORE
    assert "[마켓인사이트]" in result.keyword_hits


def test_ignore_production_line_conversion():
    """생산라인 전환 → IGNORE."""
    result = classify("LG엔솔·GM 합작법인 생산라인 전환…북미 ESS 거점 5곳 확보")
    assert result.bucket == Bucket.IGNORE
    assert "생산라인 전환" in result.keyword_hits


def test_ignore_contract_extension():
    """계약 연장 → IGNORE."""
    result = classify("A사, B사와 공급 계약 연장 체결")
    assert result.bucket == Bucket.IGNORE
    assert "계약 연장" in result.keyword_hits


def test_ignore_salary_disclosure():
    """평균연봉 공개 → IGNORE."""
    result = classify("삼성전자 평균연봉 1.3억 돌파…10년 연속 상승")
    assert result.bucket == Bucket.IGNORE
    assert "평균연봉" in result.keyword_hits


def test_ignore_broker_rating():
    """증권사 리포트(매수-유안타) → IGNORE."""
    result = classify("삼성전자 매수-유안타 목표가 95000원")
    assert result.bucket == Bucket.IGNORE
    assert "매수-유안타" in result.keyword_hits


def test_ignore_target_price_maintained():
    """목표가 유지 → IGNORE."""
    result = classify("현대차, 투자의견 및 목표가 유지")
    assert result.bucket == Bucket.IGNORE
    assert "목표가 유지" in result.keyword_hits


# ── POS_STRONG 방산/해외수주 테스트 ──────────────────

def test_pos_strong_defense_order():
    """방산 수주 → POS_STRONG."""
    result = classify("한화에어로스페이스, 폴란드 K9 추가 방산 수주 확정")
    assert result.bucket == Bucket.POS_STRONG
    assert "방산 수주" in result.keyword_hits


def test_pos_strong_overseas_order():
    """해외 수주 → POS_STRONG."""
    result = classify("HD현대건설기계, 중동 대규모 해외 수주 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "해외 수주" in result.keyword_hits


def test_pos_strong_export_contract():
    """수출 계약 → POS_STRONG."""
    result = classify("A사, 미국 대상 5000억 규모 수출 계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "수출 계약" in result.keyword_hits


def test_pos_strong_exclusive_contract():
    """독점 계약 → POS_STRONG."""
    result = classify("B사, 글로벌 빅파마와 독점 계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "독점 계약" in result.keyword_hits


def test_pos_strong_record_order_backlog():
    """역대 최대 수주 → POS_STRONG."""
    result = classify("현대중공업, 역대 최대 수주 달성 공시")
    assert result.bucket == Bucket.POS_STRONG
    assert "역대 최대 수주" in result.keyword_hits


# ── IGNORE_OVERRIDE 확장 테스트 ──────────────────

def test_ignore_override_lp_contract_conclusion():
    """유동성공급계약 체결은 IGNORE (POS 공급계약 오매칭 방지)."""
    result = classify("삼성증권 ETF 유동성공급계약 체결")
    assert result.bucket == Bucket.IGNORE
    assert "유동성공급계약 체결" in result.keyword_hits


# ── UNKNOWN 버킷 축소: 신규 IGNORE 키워드 ──────────────────

def test_ignore_shareholder_meeting_concentration_day():
    """주주총회 집중일 개최 사유 신고 → IGNORE."""
    result = classify("(주)위세아이텍 주주총회 집중일 개최 사유 신고")
    assert result.bucket == Bucket.IGNORE


def test_ignore_morning_briefing():
    """[아침밥] 증권사 브리핑 → IGNORE."""
    result = classify('[아침밥] 유안타 "헥토이노베이션 사상 최대 실적에 스테이블코인 기대"')
    assert result.bucket == Bucket.IGNORE


def test_ignore_stock_show():
    """[생생한 주식쇼] → IGNORE."""
    result = classify("[생생한 주식쇼 생쇼] 변동성 활용, 실적 기반 바닥주 공략 전략")
    assert result.bucket == Bucket.IGNORE


def test_ignore_stock_magnifier():
    """[종목돋보기] → IGNORE."""
    result = classify("[종목돋보기]한주에이알티 베일 뒤로 향하는 90억")
    assert result.bucket == Bucket.IGNORE


def test_ignore_advance_payment():
    """선급금 지급 → IGNORE."""
    result = classify("엘아이지넥스원(주) 선급금 지급 결정")
    assert result.bucket == Bucket.IGNORE


def test_ignore_delisting_cleanup_trading():
    """정리매매 → IGNORE."""
    result = classify("(주)ABC 정리매매 절차 안내")
    assert result.bucket == Bucket.IGNORE


def test_ignore_business_suspension():
    """영업정지 → IGNORE."""
    result = classify("(주)경동인베스트 영업정지(종속회사의 주요경영사항)")
    assert result.bucket == Bucket.IGNORE


def test_ignore_subsidiary_inclusion():
    """지주회사의 자회사 편입 → IGNORE."""
    result = classify("JW홀딩스(주) 지주회사의 자회사 편입")
    assert result.bucket == Bucket.IGNORE


def test_ignore_kospi_summary():
    """[코스피 전 거래일 주요공시] → IGNORE."""
    result = classify("[코스피코스닥 전 거래일(12일) 주요공시]")
    assert result.bucket == Bucket.IGNORE


def test_ignore_record_date_setting():
    """기준일 설정 → IGNORE."""
    result = classify("주식회사 디앤디플랫폼위탁관리부동산투자회사 주주명부폐쇄기간 또는 기준일 설정")
    assert result.bucket == Bucket.IGNORE


def test_ignore_business_status_ir():
    """경영현황 → IGNORE."""
    result = classify("[IR일정] 오픈엣지테크놀로지, 주요 경영현황과 2025년 연간 경영실적 설명")
    assert result.bucket == Bucket.IGNORE
