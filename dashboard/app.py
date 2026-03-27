"""Kindshot 트레이딩 대시보드 — Streamlit."""

from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from data_loader import (
    available_dates,
    compute_trade_pnl,
    load_context_cards,
    load_events,
    load_health,
    load_multi_day_events,
    load_price_snapshots,
)

# ── 페이지 설정 ──────────────────────────────────────
st.set_page_config(
    page_title="Kindshot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 사이드바: 날짜 선택 ──────────────────────────────
st.sidebar.title("Kindshot")
dates = available_dates()
if not dates:
    st.error("로그 파일이 없습니다. logs/ 디렉토리를 확인하세요.")
    st.stop()

selected_date = st.sidebar.selectbox(
    "날짜 선택",
    dates,
    format_func=lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}",
)
multi_day_n = st.sidebar.slider("멀티데이 분석 (일수)", 1, min(14, len(dates)), min(7, len(dates)))

# ── 데이터 로드 ──────────────────────────────────────

@st.cache_data(ttl=60)
def _load_events(d: str) -> pd.DataFrame:
    return load_events(d)

@st.cache_data(ttl=60)
def _load_ctx(d: str) -> pd.DataFrame:
    return load_context_cards(d)

@st.cache_data(ttl=60)
def _load_pnl(d: str) -> pd.DataFrame:
    return compute_trade_pnl(d)

@st.cache_data(ttl=60)
def _load_multi(n: int) -> pd.DataFrame:
    return load_multi_day_events(n)

events_df = _load_events(selected_date)
ctx_df = _load_ctx(selected_date)
pnl_df = _load_pnl(selected_date)
multi_df = _load_multi(multi_day_n)

# ── 탭 구성 ──────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 시그널 현황",
    "💰 매매 성과",
    "📉 기술지표",
    "🖥️ 시스템 상태",
])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1: 시그널 현황
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab1:
    st.header(f"시그널 현황 — {selected_date[:4]}-{selected_date[4:6]}-{selected_date[6:]}")

    if events_df.empty:
        st.info("해당 날짜에 이벤트가 없습니다.")
    else:
        # KPI 카드
        total = len(events_df)
        buy_count = len(events_df[events_df["decision_action"] == "BUY"])
        skip_count = len(events_df[events_df["decision_action"] == "SKIP"])
        bucket_skip = len(events_df[events_df["skip_stage"] == "BUCKET"])
        quant_skip = len(events_df[events_df["skip_stage"] == "QUANT"])
        dup_skip = len(events_df[events_df["skip_stage"] == "DUPLICATE"])

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("전체 이벤트", total)
        c2.metric("BUY", buy_count, delta=f"{buy_count/total*100:.1f}%" if total else "0%")
        c3.metric("LLM SKIP", skip_count)
        c4.metric("버킷 필터", bucket_skip)
        c5.metric("퀀트 필터", quant_skip)
        c6.metric("중복 제거", dup_skip)

        st.divider()

        col_left, col_right = st.columns(2)

        # 버킷 분포
        with col_left:
            st.subheader("버킷 분포")
            bucket_counts = events_df["bucket"].value_counts().reset_index()
            bucket_counts.columns = ["bucket", "count"]
            bucket_colors = {
                "POS_STRONG": "#2ecc71", "POS_WEAK": "#82e0aa",
                "NEG_STRONG": "#e74c3c", "NEG_WEAK": "#f1948a",
                "UNKNOWN": "#95a5a6", "IGNORE": "#bdc3c7",
            }
            fig_bucket = px.bar(
                bucket_counts, x="bucket", y="count",
                color="bucket",
                color_discrete_map=bucket_colors,
                text="count",
            )
            fig_bucket.update_layout(showlegend=False, height=350)
            st.plotly_chart(fig_bucket, use_container_width=True)

        # BUY/SKIP 파이 차트
        with col_right:
            st.subheader("BUY vs SKIP (LLM 판단)")
            llm_decided = events_df[events_df["decision_action"].notna()]
            if not llm_decided.empty:
                action_counts = llm_decided["decision_action"].value_counts().reset_index()
                action_counts.columns = ["action", "count"]
                fig_action = px.pie(
                    action_counts, names="action", values="count",
                    color="action",
                    color_discrete_map={"BUY": "#2ecc71", "SKIP": "#e74c3c"},
                    hole=0.4,
                )
                fig_action.update_layout(height=350)
                st.plotly_chart(fig_action, use_container_width=True)
            else:
                st.info("LLM 판단 데이터 없음")

        # Confidence 분포
        st.subheader("Confidence 분포")
        conf_data = events_df[events_df["decision_confidence"].notna()].copy()
        if not conf_data.empty:
            conf_data["decision_confidence"] = pd.to_numeric(conf_data["decision_confidence"], errors="coerce")
            fig_conf = px.histogram(
                conf_data, x="decision_confidence",
                color="decision_action",
                nbins=20,
                color_discrete_map={"BUY": "#2ecc71", "SKIP": "#e74c3c"},
                barmode="overlay",
                opacity=0.7,
                labels={"decision_confidence": "Confidence", "decision_action": "Action"},
            )
            fig_conf.add_vline(x=78, line_dash="dash", line_color="orange",
                               annotation_text="min_buy=78")
            fig_conf.update_layout(height=300)
            st.plotly_chart(fig_conf, use_container_width=True)
        else:
            st.info("Confidence 데이터 없음")

        # Skip 사유 분석
        st.subheader("Skip 사유 분석")
        skip_events = events_df[events_df["skip_stage"].notna()]
        if not skip_events.empty:
            skip_counts = skip_events["skip_stage"].value_counts().reset_index()
            skip_counts.columns = ["stage", "count"]
            fig_skip = px.bar(skip_counts, x="stage", y="count", text="count",
                              color="stage")
            fig_skip.update_layout(showlegend=False, height=300)
            st.plotly_chart(fig_skip, use_container_width=True)

        # 최근 BUY 시그널 테이블
        st.subheader("BUY 시그널 상세")
        buy_events = events_df[events_df["decision_action"] == "BUY"][
            ["detected_at", "ticker", "corp_name", "headline", "bucket",
             "decision_confidence", "decision_size_hint", "decision_reason"]
        ].copy()
        if not buy_events.empty:
            buy_events.columns = ["시각", "종목코드", "종목명", "헤드라인", "버킷",
                                  "Confidence", "Size", "사유"]
            st.dataframe(buy_events, use_container_width=True, hide_index=True)
        else:
            st.info("BUY 시그널 없음")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2: 매매 성과
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab2:
    st.header("매매 성과")

    # 단일 날짜 PnL
    st.subheader(f"당일 성과 — {selected_date[:4]}-{selected_date[4:6]}-{selected_date[6:]}")
    if pnl_df.empty:
        st.info("해당 날짜에 매매 데이터가 없습니다.")
    else:
        wins = len(pnl_df[pnl_df["final_ret_pct"].notna() & (pnl_df["final_ret_pct"] > 0)])
        losses = len(pnl_df[pnl_df["final_ret_pct"].notna() & (pnl_df["final_ret_pct"] <= 0)])
        total_trades = wins + losses
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0
        avg_ret = pnl_df["final_ret_pct"].mean() if not pnl_df["final_ret_pct"].isna().all() else 0
        best_trade = pnl_df["best_ret_pct"].max() if not pnl_df["best_ret_pct"].isna().all() else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 매매", total_trades)
        c2.metric("승률", f"{win_rate:.1f}%")
        c3.metric("평균 수익률", f"{avg_ret:.2f}%")
        c4.metric("최고 수익", f"{best_trade:.2f}%")

        # 종목별 수익률 바 차트
        fig_pnl = px.bar(
            pnl_df.dropna(subset=["final_ret_pct"]),
            x="ticker", y="final_ret_pct",
            color="final_ret_pct",
            color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
            hover_data=["corp_name", "headline", "confidence", "bucket"],
            text="final_ret_pct",
            labels={"final_ret_pct": "수익률 (%)", "ticker": "종목코드"},
        )
        fig_pnl.update_layout(height=400)
        st.plotly_chart(fig_pnl, use_container_width=True)

        # 상세 테이블
        st.dataframe(
            pnl_df[["ticker", "corp_name", "headline", "confidence", "size_hint",
                     "bucket", "entry_px", "best_ret_pct", "final_ret_pct", "final_horizon"]],
            use_container_width=True, hide_index=True,
        )

    # 멀티데이 성과 추이
    st.divider()
    st.subheader(f"최근 {multi_day_n}일 추이")

    if multi_df.empty:
        st.info("멀티데이 데이터 없음")
    else:
        daily_stats = []
        for d in sorted(multi_df["date"].unique()):
            day_df = multi_df[multi_df["date"] == d]
            total_ev = len(day_df)
            buy_n = len(day_df[day_df["decision_action"] == "BUY"])
            skip_n = len(day_df[day_df["decision_action"] == "SKIP"])
            confs = day_df["decision_confidence"].dropna()
            avg_conf = confs.mean() if len(confs) > 0 else 0
            daily_stats.append({
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "총이벤트": total_ev,
                "BUY": buy_n,
                "SKIP": skip_n,
                "평균confidence": round(avg_conf, 1),
            })
        daily_df = pd.DataFrame(daily_stats)

        col1, col2 = st.columns(2)
        with col1:
            fig_daily = go.Figure()
            fig_daily.add_trace(go.Bar(x=daily_df["date"], y=daily_df["BUY"],
                                       name="BUY", marker_color="#2ecc71"))
            fig_daily.add_trace(go.Bar(x=daily_df["date"], y=daily_df["SKIP"],
                                       name="SKIP", marker_color="#e74c3c"))
            fig_daily.update_layout(barmode="stack", title="일별 BUY/SKIP",
                                    height=350, xaxis_title="날짜", yaxis_title="건수")
            st.plotly_chart(fig_daily, use_container_width=True)

        with col2:
            fig_conf_trend = px.line(
                daily_df, x="date", y="평균confidence",
                markers=True, title="일별 평균 Confidence",
            )
            fig_conf_trend.update_layout(height=350)
            st.plotly_chart(fig_conf_trend, use_container_width=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3: 기술지표
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab3:
    st.header("기술지표 분석")

    if ctx_df.empty:
        st.info("해당 날짜에 기술지표 데이터가 없습니다.")
    else:
        # 지표 요약 KPI
        rsi_vals = ctx_df["rsi_14"].dropna()
        macd_vals = ctx_df["macd_hist"].dropna()
        bb_vals = ctx_df["bb_position"].dropna()
        atr_vals = ctx_df["atr_14"].dropna()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("RSI 평균", f"{rsi_vals.mean():.1f}" if len(rsi_vals) else "N/A",
                   delta=f"n={len(rsi_vals)}")
        c2.metric("MACD Hist 평균", f"{macd_vals.mean():.2f}" if len(macd_vals) else "N/A",
                   delta=f"n={len(macd_vals)}")
        c3.metric("BB Position 평균", f"{bb_vals.mean():.1f}%" if len(bb_vals) else "N/A",
                   delta=f"n={len(bb_vals)}")
        c4.metric("ATR 평균", f"{atr_vals.mean():.2f}%" if len(atr_vals) else "N/A",
                   delta=f"n={len(atr_vals)}")

        st.divider()

        col1, col2 = st.columns(2)

        # RSI 분포
        with col1:
            if len(rsi_vals):
                fig_rsi = px.histogram(ctx_df.dropna(subset=["rsi_14"]),
                                        x="rsi_14", nbins=20,
                                        title="RSI-14 분포",
                                        color_discrete_sequence=["#3498db"])
                fig_rsi.add_vline(x=30, line_dash="dash", line_color="green",
                                  annotation_text="과매도 30")
                fig_rsi.add_vline(x=75, line_dash="dash", line_color="red",
                                  annotation_text="과매수 75")
                fig_rsi.update_layout(height=350)
                st.plotly_chart(fig_rsi, use_container_width=True)
            else:
                st.info("RSI 데이터 없음")

        # MACD 분포
        with col2:
            if len(macd_vals):
                fig_macd = px.histogram(ctx_df.dropna(subset=["macd_hist"]),
                                         x="macd_hist", nbins=20,
                                         title="MACD Histogram 분포",
                                         color_discrete_sequence=["#9b59b6"])
                fig_macd.add_vline(x=0, line_dash="dash", line_color="gray",
                                   annotation_text="0 기준선")
                fig_macd.update_layout(height=350)
                st.plotly_chart(fig_macd, use_container_width=True)
            else:
                st.info("MACD 데이터 없음")

        col3, col4 = st.columns(2)

        # Bollinger Band Position
        with col3:
            if len(bb_vals):
                fig_bb = px.histogram(ctx_df.dropna(subset=["bb_position"]),
                                       x="bb_position", nbins=20,
                                       title="Bollinger Band Position 분포 (%)",
                                       color_discrete_sequence=["#e67e22"])
                fig_bb.add_vline(x=95, line_dash="dash", line_color="red",
                                 annotation_text="상단 95%")
                fig_bb.add_vline(x=5, line_dash="dash", line_color="green",
                                 annotation_text="하단 5%")
                fig_bb.update_layout(height=350)
                st.plotly_chart(fig_bb, use_container_width=True)
            else:
                st.info("BB 데이터 없음")

        # ATR 분포
        with col4:
            if len(atr_vals):
                fig_atr = px.histogram(ctx_df.dropna(subset=["atr_14"]),
                                        x="atr_14", nbins=20,
                                        title="ATR-14 분포 (% of price)",
                                        color_discrete_sequence=["#1abc9c"])
                fig_atr.add_vline(x=5, line_dash="dash", line_color="red",
                                  annotation_text="고변동 5%")
                fig_atr.update_layout(height=350)
                st.plotly_chart(fig_atr, use_container_width=True)
            else:
                st.info("ATR 데이터 없음")

        # 버킷별 지표 scatter
        st.subheader("버킷별 RSI vs Confidence")
        merged = events_df.merge(ctx_df[["event_id", "rsi_14", "atr_14"]], on="event_id", how="inner")
        merged = merged[merged["decision_confidence"].notna() & merged["rsi_14"].notna()]
        if not merged.empty:
            fig_scatter = px.scatter(
                merged, x="rsi_14", y="decision_confidence",
                color="bucket", size="atr_14",
                hover_data=["ticker", "corp_name"],
                title="RSI vs Confidence (크기=ATR)",
            )
            fig_scatter.update_layout(height=400)
            st.plotly_chart(fig_scatter, use_container_width=True)

        # 종목별 지표 테이블
        st.subheader("종목별 기술지표")
        display_cols = ["ticker", "corp_name", "bucket", "rsi_14", "macd_hist",
                        "bb_position", "atr_14", "spread_bps", "adv_value_20d"]
        available_cols = [c for c in display_cols if c in ctx_df.columns]
        st.dataframe(ctx_df[available_cols], use_container_width=True, hide_index=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4: 시스템 상태
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab4:
    st.header("시스템 상태")

    health = load_health()

    if health:
        # 서버 상태
        status = health.get("status", "unknown")
        status_color = "🟢" if status == "healthy" else "🟡" if status == "degraded" else "🔴"
        st.subheader(f"{status_color} 서버 상태: {status.upper()}")

        # 기본 메트릭
        c1, c2, c3, c4 = st.columns(4)
        uptime_s = health.get("uptime_seconds", 0)
        uptime_h = uptime_s / 3600
        c1.metric("가동시간", f"{uptime_h:.1f}h")
        c2.metric("처리 이벤트", health.get("events_processed", 0))
        c3.metric("BUY 건수", health.get("buy_count", 0))
        c4.metric("에러 건수", health.get("error_count", 0))

        st.divider()

        col1, col2 = st.columns(2)

        # LLM 상태
        with col1:
            st.subheader("LLM 엔진")
            llm_calls = health.get("llm_calls", 0)
            llm_avg = health.get("llm_avg_ms", 0)
            llm_fb = health.get("llm_fallback_count", 0)
            cb = health.get("circuit_breaker", {})

            st.metric("LLM 호출 수", llm_calls)
            st.metric("평균 응답시간", f"{llm_avg:.0f}ms")
            st.metric("Fallback 횟수", llm_fb)

            # Circuit breaker 상태
            st.write("**Circuit Breaker:**")
            for provider, is_open in cb.items():
                icon = "🔴 OPEN" if is_open else "🟢 CLOSED"
                st.write(f"  - {provider}: {icon}")

        # Guardrail 상태
        with col2:
            st.subheader("Guardrail 상태")
            gs = health.get("guardrail_state", {})
            if gs:
                st.metric("일일 PnL", f"₩{gs.get('daily_pnl', 0):,.0f}")
                st.metric("보유 포지션", gs.get("position_count", 0))
                st.metric("연속 손절", gs.get("consecutive_stop_losses", 0))
                st.metric("매수 종목 수", gs.get("bought_tickers_count", 0))

            # Guardrail blocks
            blocks = health.get("guardrail_blocks", {})
            if blocks:
                st.write("**차단 사유:**")
                blocks_df = pd.DataFrame([
                    {"사유": k, "건수": v} for k, v in blocks.items()
                ])
                st.dataframe(blocks_df, use_container_width=True, hide_index=True)

        # KIS API 상태
        st.divider()
        st.subheader("KIS API")
        c1, c2 = st.columns(2)
        c1.metric("KIS 호출 수", health.get("kis_calls", 0))
        c2.metric("KIS 에러", health.get("kis_errors", 0))

    else:
        st.warning("서버에 연결할 수 없습니다 (127.0.0.1:8080). 오프라인 데이터를 표시합니다.")

        # 파일 기반 fallback: 로그에서 추정
        if not events_df.empty:
            st.subheader("로그 기반 시스템 현황")

            total_ev = len(events_df)
            error_ev = len(events_df[events_df.get("skip_stage") == "LLM_ERROR"]) if "skip_stage" in events_df.columns else 0
            buy_ev = len(events_df[events_df["decision_action"] == "BUY"])

            c1, c2, c3 = st.columns(3)
            c1.metric("총 이벤트 (당일)", total_ev)
            c2.metric("BUY 시그널", buy_ev)
            c3.metric("LLM 에러", error_ev)

            # Skip stage 분포 → guardrail 활동 proxy
            if "skip_stage" in events_df.columns:
                skip_data = events_df["skip_stage"].dropna().value_counts().reset_index()
                skip_data.columns = ["단계", "건수"]
                st.subheader("필터링 단계별 건수")
                st.dataframe(skip_data, use_container_width=True, hide_index=True)

            # Guardrail result 분포
            if "guardrail_result" in events_df.columns:
                gr_data = events_df["guardrail_result"].dropna()
                if len(gr_data):
                    st.subheader("Guardrail 결과")
                    gr_counts = gr_data.value_counts().reset_index()
                    gr_counts.columns = ["결과", "건수"]
                    st.dataframe(gr_counts, use_container_width=True, hide_index=True)
        else:
            st.info("표시할 데이터가 없습니다.")


# ── 푸터 ──────────────────────────────────────────────
st.sidebar.divider()
st.sidebar.caption("Kindshot Trading Dashboard v1.0")
st.sidebar.caption("streamlit run dashboard/app.py")
