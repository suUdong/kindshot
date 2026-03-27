"""Kindshot 트레이딩 대시보드 — Streamlit."""

from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from data_loader import (
    available_dates,
    compute_daily_equity_curve,
    compute_multi_day_pnl,
    compute_trade_pnl,
    load_context_cards,
    load_events,
    load_health,
    load_live_feed,
    load_multi_day_events,
    load_multi_day_pnl_detail,
    load_price_snapshots,
    load_shadow_trade_pnl,
    load_version_trend,
    summarize_shadow_trade_pnl,
)

# ── 페이지 설정 ──────────────────────────────────────
st.set_page_config(
    page_title="Kindshot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --kindshot-bg: #f4efe6;
        --kindshot-panel: #fffdf8;
        --kindshot-text: #1f2933;
        --kindshot-muted: #5f6c7b;
        --kindshot-accent: #c65d2e;
        --kindshot-line: rgba(31, 41, 51, 0.08);
    }
    .stApp {
        background:
            radial-gradient(circle at top right, rgba(198, 93, 46, 0.12), transparent 28%),
            linear-gradient(180deg, #f8f3eb 0%, var(--kindshot-bg) 100%);
        color: var(--kindshot-text);
    }
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 2rem;
    }
    div[data-testid="stMetric"] {
        background: rgba(255, 253, 248, 0.85);
        border: 1px solid var(--kindshot-line);
        border-radius: 18px;
        padding: 0.7rem 0.9rem;
        box-shadow: 0 10px 30px rgba(31, 41, 51, 0.04);
    }
    div[data-testid="stDataFrame"] {
        border-radius: 18px;
        overflow: hidden;
    }
    @media (max-width: 768px) {
        .block-container {
            padding-left: 0.8rem;
            padding-right: 0.8rem;
        }
        h1, h2, h3 {
            letter-spacing: -0.02em;
        }
        div[data-testid="stMetric"] {
            padding: 0.65rem 0.75rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
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

# 새로고침
if st.sidebar.button("🔄 데이터 새로고침"):
    st.cache_data.clear()
    st.rerun()

auto_refresh = st.sidebar.checkbox("자동 새로고침 (60초)", value=False)
if auto_refresh:
    import time as _time
    _refresh_key = f"auto_refresh_{int(_time.time()) // 60}"
    st.sidebar.caption(f"다음 갱신까지 ~60초")
    _time.sleep(60)
    st.cache_data.clear()
    st.rerun()

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

@st.cache_data(ttl=60)
def _load_multi_pnl(n: int) -> pd.DataFrame:
    return compute_multi_day_pnl(n)

@st.cache_data(ttl=60)
def _load_daily_equity(d: str) -> pd.DataFrame:
    return compute_daily_equity_curve(d)

@st.cache_data(ttl=60)
def _load_shadow(d: str) -> pd.DataFrame:
    return load_shadow_trade_pnl(d)

@st.cache_data(ttl=60)
def _load_shadow_summary(d: str) -> dict:
    return summarize_shadow_trade_pnl(d)

@st.cache_data(ttl=60)
def _load_live_feed(limit: int, n_days: int) -> pd.DataFrame:
    return load_live_feed(limit=limit, n_days=n_days)

@st.cache_data(ttl=60)
def _load_versions() -> pd.DataFrame:
    return load_version_trend()

events_df = _load_events(selected_date)
ctx_df = _load_ctx(selected_date)
pnl_df = _load_pnl(selected_date)
multi_df = _load_multi(multi_day_n)
multi_pnl_df = _load_multi_pnl(multi_day_n)
daily_equity_df = _load_daily_equity(selected_date)
shadow_df = _load_shadow(selected_date)
shadow_summary = _load_shadow_summary(selected_date)
live_feed_df = _load_live_feed(40, min(3, len(dates)))
version_trend_df = _load_versions()

# ── 탭 구성 ──────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 시그널 현황",
    "💰 매매 성과",
    "📉 기술지표",
    "🖥️ 시스템 상태",
    "🔬 전략 분석",
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
        has_effective = "effective_action" in events_df.columns
        if has_effective:
            buy_count = len(events_df[events_df["effective_action"] == "BUY"])
            guardrail_blocked = len(events_df[events_df["effective_action"] == "GUARDRAIL_BLOCKED"])
        else:
            buy_count = len(events_df[events_df["decision_action"] == "BUY"])
            guardrail_blocked = 0
        skip_count = len(events_df[events_df["decision_action"] == "SKIP"])
        bucket_skip = len(events_df[events_df["skip_stage"] == "BUCKET"])
        quant_skip = len(events_df[events_df["skip_stage"] == "QUANT"])
        dup_skip = len(events_df[events_df["skip_stage"] == "DUPLICATE"])

        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        c1.metric("전체 이벤트", total)
        c2.metric("BUY (실행)", buy_count, delta=f"{buy_count/total*100:.1f}%" if total else "0%")
        c3.metric("BUY (차단)", guardrail_blocked)
        c4.metric("LLM SKIP", skip_count)
        c5.metric("버킷 필터", bucket_skip)
        c6.metric("퀀트 필터", quant_skip)
        c7.metric("중복 제거", dup_skip)

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

        # 파이프라인 퍼널
        st.subheader("파이프라인 퍼널")
        llm_reached = len(events_df[events_df["decision_action"].notna()])
        guardrail_skip = len(events_df[events_df["skip_stage"] == "GUARDRAIL"])
        funnel_data = pd.DataFrame({
            "단계": ["전체 이벤트", "중복 제거 통과", "버킷 필터 통과", "퀀트 필터 통과",
                    "LLM 판단", "Guardrail 통과", "BUY 실행"],
            "건수": [
                total,
                total - dup_skip,
                total - dup_skip - bucket_skip,
                total - dup_skip - bucket_skip - quant_skip,
                llm_reached,
                llm_reached - guardrail_skip,
                buy_count,
            ],
        })
        fig_funnel = go.Figure(go.Funnel(
            y=funnel_data["단계"], x=funnel_data["건수"],
            textinfo="value+percent initial",
            marker=dict(color=["#3498db", "#2980b9", "#1abc9c", "#16a085", "#f39c12", "#27ae60", "#2ecc71"]),
        ))
        fig_funnel.update_layout(height=350)
        st.plotly_chart(fig_funnel, use_container_width=True)

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

        # Decision Source 분석
        if "decision_source" in events_df.columns:
            src_data = events_df[events_df["decision_source"].notna()]
            if not src_data.empty:
                st.subheader("Decision Source 분석")
                col_src1, col_src2 = st.columns(2)
                with col_src1:
                    src_counts = src_data["decision_source"].value_counts().reset_index()
                    src_counts.columns = ["source", "count"]
                    fig_src = px.pie(src_counts, names="source", values="count",
                                    title="판단 경로 비율", hole=0.4,
                                    color_discrete_sequence=px.colors.qualitative.Set2)
                    fig_src.update_layout(height=300)
                    st.plotly_chart(fig_src, use_container_width=True)
                with col_src2:
                    src_action = src_data.groupby(["decision_source", "decision_action"]).size().reset_index(name="count")
                    fig_src_act = px.bar(src_action, x="decision_source", y="count",
                                         color="decision_action", barmode="group",
                                         color_discrete_map={"BUY": "#2ecc71", "SKIP": "#e74c3c"},
                                         title="Source별 BUY/SKIP")
                    fig_src_act.update_layout(height=300)
                    st.plotly_chart(fig_src_act, use_container_width=True)

                # LLM 레이턴시 분포
                if "llm_latency_ms" in events_df.columns:
                    lat_data = events_df["llm_latency_ms"].dropna()
                    if len(lat_data):
                        st.subheader("LLM 레이턴시 분포")
                        fig_lat = px.histogram(lat_data, nbins=30,
                                               title="LLM 응답 시간 (ms)",
                                               color_discrete_sequence=["#3498db"])
                        fig_lat.update_layout(height=250)
                        st.plotly_chart(fig_lat, use_container_width=True)

        # 최근 BUY 시그널 테이블
        st.subheader("BUY 시그널 상세")
        buy_cols = ["detected_at", "ticker", "corp_name", "headline", "bucket",
                    "decision_confidence", "decision_size_hint", "decision_reason"]
        if "decision_source" in events_df.columns:
            buy_cols.append("decision_source")
        buy_events = events_df[events_df["decision_action"] == "BUY"][
            [c for c in buy_cols if c in events_df.columns]
        ].copy()
        if not buy_events.empty:
            col_names = ["시각", "종목코드", "종목명", "헤드라인", "버킷",
                         "Confidence", "Size", "사유"]
            if "decision_source" in buy_events.columns:
                col_names.append("Source")
            buy_events.columns = col_names[:len(buy_events.columns)]
            st.dataframe(buy_events, use_container_width=True, hide_index=True)
        else:
            st.info("BUY 시그널 없음")

    st.divider()
    st.subheader("실시간 뉴스 피드 모니터")
    if live_feed_df.empty:
        st.info("최근 이벤트 피드가 없습니다.")
    else:
        latest_ts = live_feed_df["detected_at"].dropna().max() if "detected_at" in live_feed_df.columns else None
        feed_exec = int((live_feed_df["feed_action"] == "BUY").sum()) if "feed_action" in live_feed_df.columns else 0
        feed_blocked = int((live_feed_df["feed_action"] == "GUARDRAIL_BLOCKED").sum()) if "feed_action" in live_feed_df.columns else 0
        feed_sources = int(live_feed_df["source"].nunique()) if "source" in live_feed_df.columns else 0

        fc1, fc2, fc3, fc4 = st.columns(4)
        fc1.metric("최근 이벤트", len(live_feed_df))
        fc2.metric("실행 BUY", feed_exec)
        fc3.metric("차단 BUY", feed_blocked)
        fc4.metric("활성 소스", feed_sources, delta=latest_ts.strftime("%m-%d %H:%M") if latest_ts is not None else None)

        feed_display = live_feed_df.copy()
        if "detected_at" in feed_display.columns:
            feed_display["detected_at"] = feed_display["detected_at"].dt.strftime("%m-%d %H:%M:%S")
        rename_map = {
            "date": "일자",
            "detected_at": "시각",
            "source": "소스",
            "ticker": "종목코드",
            "corp_name": "종목명",
            "headline": "헤드라인",
            "bucket": "버킷",
            "feed_action": "결과",
            "decision_confidence": "Confidence",
            "guardrail_result": "차단사유",
        }
        feed_display = feed_display.rename(columns=rename_map)
        st.dataframe(feed_display, use_container_width=True, hide_index=True, height=420)


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

        if not daily_equity_df.empty:
            st.subheader("실시간 P&L 차트")
            eq_col1, eq_col2 = st.columns(2)
            with eq_col1:
                fig_intraday = go.Figure()
                fig_intraday.add_trace(go.Scatter(
                    x=daily_equity_df["detected_at"],
                    y=daily_equity_df["cum_ret_pct"],
                    mode="lines+markers",
                    name="당일 누적 수익률",
                    line=dict(color="#c65d2e", width=3),
                    fill="tozeroy",
                    fillcolor="rgba(198,93,46,0.12)",
                    customdata=daily_equity_df[["ticker", "headline"]],
                    hovertemplate="%{x}<br>%{customdata[0]}<br>%{customdata[1]}<br>누적 %{y:.2f}%<extra></extra>",
                ))
                fig_intraday.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_intraday.update_layout(
                    title="당일 수익곡선",
                    height=360,
                    yaxis_title="누적 수익률 (%)",
                    xaxis_title="시각",
                    margin=dict(l=10, r=10, t=48, b=10),
                )
                st.plotly_chart(fig_intraday, use_container_width=True)
            with eq_col2:
                fig_daily_dd = go.Figure()
                fig_daily_dd.add_trace(go.Scatter(
                    x=daily_equity_df["detected_at"],
                    y=daily_equity_df["drawdown_pct"],
                    mode="lines+markers",
                    name="당일 드로다운",
                    line=dict(color="#264653", width=3),
                    fill="tozeroy",
                    fillcolor="rgba(38,70,83,0.14)",
                    hovertemplate="%{x}<br>드로다운 %{y:.2f}%<extra></extra>",
                ))
                fig_daily_dd.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_daily_dd.update_layout(
                    title="당일 드로다운",
                    height=360,
                    yaxis_title="드로다운 (%)",
                    xaxis_title="시각",
                    margin=dict(l=10, r=10, t=48, b=10),
                )
                st.plotly_chart(fig_daily_dd, use_container_width=True)

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
        pnl_display = pnl_df[["ticker", "corp_name", "headline", "confidence", "size_hint",
                               "bucket", "entry_px", "best_ret_pct", "final_ret_pct", "final_horizon"]]
        st.dataframe(pnl_display, use_container_width=True, hide_index=True)
        st.download_button(
            "CSV 다운로드 (당일 매매)",
            pnl_display.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"kindshot_trades_{selected_date}.csv",
            mime="text/csv",
        )

        # ── 수익성 심층 분석 ──
        st.divider()
        st.subheader("수익성 심층 분석")

        valid_pnl = pnl_df.dropna(subset=["final_ret_pct"])
        if not valid_pnl.empty:
            col_a, col_b = st.columns(2)

            # Confidence vs 실제 수익률 scatter (캘리브레이션)
            with col_a:
                conf_vals = pd.to_numeric(valid_pnl["confidence"], errors="coerce")
                fig_cal = px.scatter(
                    valid_pnl, x=conf_vals, y="final_ret_pct",
                    color="bucket",
                    hover_data=["ticker", "corp_name"],
                    title="Confidence vs 실제 수익률 (캘리브레이션)",
                    labels={"x": "Confidence", "final_ret_pct": "수익률 (%)"},
                    trendline="ols",
                )
                fig_cal.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_cal.update_layout(height=400)
                st.plotly_chart(fig_cal, use_container_width=True)

            # 버킷별 성과 박스플롯
            with col_b:
                fig_bucket_perf = px.box(
                    valid_pnl, x="bucket", y="final_ret_pct",
                    color="bucket",
                    title="버킷별 수익률 분포",
                    labels={"final_ret_pct": "수익률 (%)", "bucket": "버킷"},
                    points="all",
                )
                fig_bucket_perf.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_bucket_perf.update_layout(height=400, showlegend=False)
                st.plotly_chart(fig_bucket_perf, use_container_width=True)

            # 시간대별 분석 (detected_at 기반)
            if "detected_at" in events_df.columns:
                buy_with_time = events_df[events_df["decision_action"] == "BUY"].copy()
                buy_with_time = buy_with_time.merge(
                    valid_pnl[["event_id", "final_ret_pct"]], on="event_id", how="inner"
                )
                if not buy_with_time.empty and buy_with_time["detected_at"].notna().any():
                    buy_with_time["hour"] = buy_with_time["detected_at"].dt.hour
                    hourly = buy_with_time.groupby("hour").agg(
                        trades=("final_ret_pct", "count"),
                        avg_ret=("final_ret_pct", "mean"),
                        win_rate=("final_ret_pct", lambda x: (x > 0).mean() * 100),
                    ).reset_index()

                    col_t1, col_t2 = st.columns(2)
                    with col_t1:
                        fig_hour_ret = px.bar(
                            hourly, x="hour", y="avg_ret",
                            text=[f"{v:.2f}%" for v in hourly["avg_ret"]],
                            title="시간대별 평균 수익률",
                            labels={"hour": "시간 (KST)", "avg_ret": "평균 수익률 (%)"},
                            color="avg_ret",
                            color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                        )
                        fig_hour_ret.update_layout(height=350)
                        st.plotly_chart(fig_hour_ret, use_container_width=True)

                    with col_t2:
                        fig_hour_wr = px.bar(
                            hourly, x="hour", y="win_rate",
                            text=[f"{v:.0f}%" for v in hourly["win_rate"]],
                            title="시간대별 승률",
                            labels={"hour": "시간 (KST)", "win_rate": "승률 (%)"},
                            color="win_rate",
                            color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                            range_color=[0, 100],
                        )
                        fig_hour_wr.add_hline(y=50, line_dash="dash", line_color="orange")
                        fig_hour_wr.update_layout(height=350)
                        st.plotly_chart(fig_hour_wr, use_container_width=True)

            # Size Hint 효과 분석
            if "size_hint" in valid_pnl.columns:
                size_stats = valid_pnl.groupby("size_hint").agg(
                    trades=("final_ret_pct", "count"),
                    avg_ret=("final_ret_pct", "mean"),
                    win_rate=("final_ret_pct", lambda x: (x > 0).mean() * 100),
                ).reset_index()
                if not size_stats.empty:
                    st.subheader("Size Hint별 성과")
                    size_stats.columns = ["Size", "매매수", "평균수익률(%)", "승률(%)"]
                    size_stats["평균수익률(%)"] = size_stats["평균수익률(%)"].round(2)
                    size_stats["승률(%)"] = size_stats["승률(%)"].round(1)
                    st.dataframe(size_stats, use_container_width=True, hide_index=True)

            # 키워드별 성과 분석
            if "keyword_hits" in events_df.columns:
                buy_kw = events_df[events_df["decision_action"] == "BUY"].copy()
                buy_kw = buy_kw.merge(
                    valid_pnl[["event_id", "final_ret_pct"]], on="event_id", how="inner"
                )
                kw_rows = []
                for _, row in buy_kw.iterrows():
                    hits = row.get("keyword_hits")
                    if isinstance(hits, list):
                        for kw in hits:
                            kw_rows.append({"keyword": kw, "ret": row["final_ret_pct"]})
                if kw_rows:
                    kw_df = pd.DataFrame(kw_rows)
                    kw_stats = kw_df.groupby("keyword").agg(
                        trades=("ret", "count"),
                        avg_ret=("ret", "mean"),
                        win_rate=("ret", lambda x: (x > 0).mean() * 100),
                        total_ret=("ret", "sum"),
                    ).reset_index().sort_values("total_ret", ascending=False)

                    st.subheader("키워드별 성과")
                    col_kw1, col_kw2 = st.columns(2)
                    with col_kw1:
                        top_kw = kw_stats.head(15)
                        fig_kw = px.bar(
                            top_kw, x="keyword", y="total_ret",
                            color="avg_ret",
                            color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                            hover_data=["trades", "win_rate"],
                            title="키워드별 총 수익률 (Top 15)",
                            labels={"total_ret": "총 수익률 (%)", "keyword": "키워드"},
                        )
                        fig_kw.update_layout(height=400)
                        st.plotly_chart(fig_kw, use_container_width=True)

                    with col_kw2:
                        kw_display = kw_stats.copy()
                        kw_display.columns = ["키워드", "매매수", "평균수익률(%)", "승률(%)", "총수익률(%)"]
                        for c in ["평균수익률(%)", "승률(%)", "총수익률(%)"]:
                            kw_display[c] = kw_display[c].round(2)
                        st.dataframe(kw_display, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Shadow Snapshot 기회비용")
        blocked_buy_count = shadow_summary.get("blocked_buy_count", 0)
        if shadow_df.empty:
            if blocked_buy_count > 0:
                st.warning(f"차단된 BUY는 {blocked_buy_count}건 있었지만 아직 shadow snapshot 결과가 없습니다.")
            else:
                st.info("차단된 BUY shadow snapshot 데이터가 없습니다.")
        else:
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("차단 BUY", blocked_buy_count)
            sc2.metric("가상 승률", f"{shadow_summary.get('win_rate', 0.0):.1f}%")
            sc3.metric("가상 총수익률", f"{shadow_summary.get('total_ret_pct', 0.0):.2f}%")
            sc4.metric("대표 차단사유", shadow_summary.get("top_guardrail_reason") or "N/A")

            shadow_chart_col, shadow_table_col = st.columns([1.15, 1])
            with shadow_chart_col:
                shadow_valid = shadow_df.dropna(subset=["final_ret_pct"])
                fig_shadow = px.bar(
                    shadow_valid,
                    x="ticker",
                    y="final_ret_pct",
                    color="guardrail_result",
                    hover_data=["headline", "confidence", "best_ret_pct", "final_horizon"],
                    title="차단 BUY 가상 수익률",
                    labels={"final_ret_pct": "가상 수익률 (%)", "ticker": "종목코드"},
                )
                fig_shadow.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_shadow.update_layout(height=360, margin=dict(l=10, r=10, t=48, b=10))
                st.plotly_chart(fig_shadow, use_container_width=True)
            with shadow_table_col:
                shadow_display = shadow_df[[
                    "ticker", "corp_name", "headline", "confidence",
                    "guardrail_result", "best_ret_pct", "final_ret_pct", "final_horizon",
                ]].rename(columns={
                    "ticker": "종목코드",
                    "corp_name": "종목명",
                    "headline": "헤드라인",
                    "confidence": "Confidence",
                    "guardrail_result": "차단사유",
                    "best_ret_pct": "최대수익(%)",
                    "final_ret_pct": "가상수익(%)",
                    "final_horizon": "종결시점",
                })
                st.dataframe(shadow_display, use_container_width=True, hide_index=True, height=360)

        st.divider()
        st.subheader("버전 성과 추이 (v64 → v65 → v66)")
        vt_col1, vt_col2 = st.columns([1.2, 1])
        with vt_col1:
            fig_version = go.Figure()
            fig_version.add_trace(go.Scatter(
                x=version_trend_df["version"],
                y=version_trend_df["win_rate"],
                mode="lines+markers",
                name="승률",
                line=dict(color="#c65d2e", width=3),
                hovertemplate="%{x}<br>승률 %{y:.1f}%<extra></extra>",
            ))
            fig_version.add_trace(go.Scatter(
                x=version_trend_df["version"],
                y=version_trend_df["total_ret_pct"],
                mode="lines+markers",
                name="총수익률",
                line=dict(color="#264653", width=3),
                yaxis="y2",
                hovertemplate="%{x}<br>총수익률 %{y:.2f}%<extra></extra>",
            ))
            fig_version.update_layout(
                height=360,
                title="승률 / 총수익률 트렌드",
                yaxis=dict(title="승률 (%)"),
                yaxis2=dict(title="총수익률 (%)", overlaying="y", side="right"),
                margin=dict(l=10, r=10, t=48, b=10),
            )
            st.plotly_chart(fig_version, use_container_width=True)
        with vt_col2:
            version_display = version_trend_df.copy()
            version_display["win_rate"] = version_display["win_rate"].map(lambda v: f"{v:.1f}%" if pd.notna(v) else "N/A")
            version_display["total_ret_pct"] = version_display["total_ret_pct"].map(lambda v: f"{v:.2f}%" if pd.notna(v) else "N/A")
            version_display["mdd_pct"] = version_display["mdd_pct"].map(lambda v: f"{v:.2f}%" if pd.notna(v) else "N/A")
            version_display = version_display.rename(columns={
                "version": "버전",
                "win_rate": "승률",
                "total_ret_pct": "총수익률",
                "mdd_pct": "MDD",
                "sample_size": "표본수",
                "source": "근거",
                "notes": "메모",
            })
            st.dataframe(version_display, use_container_width=True, hide_index=True, height=360)

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
            if "effective_action" in day_df.columns:
                buy_exec = len(day_df[day_df["effective_action"] == "BUY"])
                buy_blocked = len(day_df[day_df["effective_action"] == "GUARDRAIL_BLOCKED"])
            else:
                buy_exec = len(day_df[day_df["decision_action"] == "BUY"])
                buy_blocked = 0
            skip_n = len(day_df[day_df["decision_action"] == "SKIP"])
            confs = day_df["decision_confidence"].dropna()
            avg_conf = confs.mean() if len(confs) > 0 else 0
            daily_stats.append({
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "총이벤트": total_ev,
                "BUY실행": buy_exec,
                "BUY차단": buy_blocked,
                "SKIP": skip_n,
                "평균confidence": round(avg_conf, 1),
            })
        daily_df = pd.DataFrame(daily_stats)

        col1, col2 = st.columns(2)
        with col1:
            fig_daily = go.Figure()
            fig_daily.add_trace(go.Bar(x=daily_df["date"], y=daily_df["BUY실행"],
                                       name="BUY 실행", marker_color="#2ecc71"))
            fig_daily.add_trace(go.Bar(x=daily_df["date"], y=daily_df["BUY차단"],
                                       name="BUY 차단", marker_color="#f39c12"))
            fig_daily.add_trace(go.Bar(x=daily_df["date"], y=daily_df["SKIP"],
                                       name="SKIP", marker_color="#e74c3c"))
            fig_daily.update_layout(barmode="stack", title="일별 BUY 실행/차단/SKIP",
                                    height=350, xaxis_title="날짜", yaxis_title="건수")
            st.plotly_chart(fig_daily, use_container_width=True)

        with col2:
            fig_conf_trend = px.line(
                daily_df, x="date", y="평균confidence",
                markers=True, title="일별 평균 Confidence",
            )
            fig_conf_trend.update_layout(height=350)
            st.plotly_chart(fig_conf_trend, use_container_width=True)

        # 주간/멀티데이 곡선
        if not multi_pnl_df.empty and multi_pnl_df["trades"].sum() > 0:
            st.subheader("주간 수익곡선 / 드로다운")

            col_pnl1, col_pnl2 = st.columns(2)
            with col_pnl1:
                fig_cum = go.Figure()
                fig_cum.add_trace(go.Scatter(
                    x=multi_pnl_df["date"], y=multi_pnl_df["cum_ret_pct"],
                    mode="lines+markers", name="누적 수익률",
                    line=dict(color="#3498db", width=3),
                    fill="tozeroy",
                    fillcolor="rgba(52,152,219,0.1)",
                ))
                fig_cum.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_cum.update_layout(
                    title="누적 수익률 (%)",
                    height=400, yaxis_title="수익률 (%)", xaxis_title="날짜",
                )
                st.plotly_chart(fig_cum, use_container_width=True)

            with col_pnl2:
                fig_weekly_dd = go.Figure()
                fig_weekly_dd.add_trace(go.Scatter(
                    x=multi_pnl_df["date"], y=multi_pnl_df["drawdown_pct"],
                    mode="lines+markers", name="주간 드로다운",
                    line=dict(color="#264653", width=3),
                    fill="tozeroy",
                    fillcolor="rgba(38,70,83,0.14)",
                ))
                fig_weekly_dd.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_weekly_dd.update_layout(
                    title="주간 드로다운 (%)",
                    height=400, yaxis_title="드로다운 (%)", xaxis_title="날짜",
                )
                st.plotly_chart(fig_weekly_dd, use_container_width=True)

            fig_daily_pnl = go.Figure()
            colors = ["#2ecc71" if v >= 0 else "#e74c3c"
                      for v in multi_pnl_df["total_ret_pct"]]
            fig_daily_pnl.add_trace(go.Bar(
                x=multi_pnl_df["date"], y=multi_pnl_df["total_ret_pct"],
                name="일별 수익률", marker_color=colors,
                text=[f"{v:.2f}%" for v in multi_pnl_df["total_ret_pct"]],
                textposition="outside",
            ))
            fig_daily_pnl.add_hline(y=0, line_dash="dash", line_color="gray")
            fig_daily_pnl.update_layout(
                title="일별 수익률 (%)",
                height=360, yaxis_title="수익률 (%)", xaxis_title="날짜",
            )
            st.plotly_chart(fig_daily_pnl, use_container_width=True)

            # 승률 추이
            wr_data = multi_pnl_df[multi_pnl_df["trades"] > 0]
            if not wr_data.empty:
                fig_wr = px.bar(
                    wr_data, x="date", y="win_rate",
                    text=[f"{v:.0f}%" for v in wr_data["win_rate"]],
                    title="일별 승률 (%)",
                    color="win_rate",
                    color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                    range_color=[0, 100],
                )
                fig_wr.add_hline(y=50, line_dash="dash", line_color="orange",
                                 annotation_text="50%")
                fig_wr.update_layout(height=350)
                st.plotly_chart(fig_wr, use_container_width=True)


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
            error_ev = len(events_df[events_df["skip_stage"] == "LLM_ERROR"]) if "skip_stage" in events_df.columns else 0
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 5: 전략 분석 (멀티데이 통합)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab5:
    st.header(f"전략 분석 — 최근 {multi_day_n}일 통합")

    @st.cache_data(ttl=60)
    def _load_detail(n: int) -> pd.DataFrame:
        return load_multi_day_pnl_detail(n)

    detail_df = _load_detail(multi_day_n)

    if detail_df.empty:
        st.info("매매 데이터가 없습니다.")
    else:
        valid_detail = detail_df.dropna(subset=["final_ret_pct"])
        total_t = len(valid_detail)
        total_wins = int((valid_detail["final_ret_pct"] > 0).sum())
        total_wr = total_wins / total_t * 100 if total_t else 0
        total_cum = valid_detail["final_ret_pct"].sum()
        avg_ret = valid_detail["final_ret_pct"].mean()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 매매", total_t)
        c2.metric("통합 승률", f"{total_wr:.1f}%")
        c3.metric("누적 수익률", f"{total_cum:.2f}%")
        c4.metric("평균 수익률", f"{avg_ret:.2f}%")

        st.divider()

        # 버킷별 통합 성과
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("버킷별 통합 성과")
            bucket_perf = valid_detail.groupby("bucket").agg(
                trades=("final_ret_pct", "count"),
                avg_ret=("final_ret_pct", "mean"),
                total_ret=("final_ret_pct", "sum"),
                win_rate=("final_ret_pct", lambda x: (x > 0).mean() * 100),
            ).reset_index().sort_values("total_ret", ascending=False)
            fig_bp = px.bar(bucket_perf, x="bucket", y="total_ret",
                            color="win_rate", text=[f"{v:.1f}%" for v in bucket_perf["total_ret"]],
                            color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                            range_color=[0, 100],
                            title="버킷별 총 수익률 (색상=승률)",
                            labels={"total_ret": "총 수익률 (%)"})
            fig_bp.update_layout(height=400)
            st.plotly_chart(fig_bp, use_container_width=True)

        # Confidence 구간별 성과
        with col2:
            st.subheader("Confidence 구간별 성과")
            conf_numeric = pd.to_numeric(valid_detail["confidence"], errors="coerce")
            valid_detail = valid_detail.copy()
            valid_detail["conf_bin"] = pd.cut(conf_numeric, bins=[0, 70, 78, 85, 90, 100],
                                              labels=["<70", "70-78", "78-85", "85-90", "90+"])
            conf_perf = valid_detail.groupby("conf_bin", observed=True).agg(
                trades=("final_ret_pct", "count"),
                avg_ret=("final_ret_pct", "mean"),
                win_rate=("final_ret_pct", lambda x: (x > 0).mean() * 100),
            ).reset_index()
            fig_cp = go.Figure()
            fig_cp.add_trace(go.Bar(x=conf_perf["conf_bin"].astype(str), y=conf_perf["avg_ret"],
                                     name="평균수익률(%)", marker_color="#3498db",
                                     text=[f"{v:.2f}%" for v in conf_perf["avg_ret"]],
                                     textposition="outside"))
            fig_cp.add_trace(go.Scatter(x=conf_perf["conf_bin"].astype(str), y=conf_perf["win_rate"],
                                         name="승률(%)", yaxis="y2", mode="lines+markers",
                                         marker_color="#e74c3c"))
            fig_cp.update_layout(
                title="Confidence 구간별 수익률/승률",
                yaxis=dict(title="평균 수익률 (%)"),
                yaxis2=dict(title="승률 (%)", overlaying="y", side="right", range=[0, 100]),
                height=400,
            )
            st.plotly_chart(fig_cp, use_container_width=True)

        # 시간대별 통합 성과
        if "detected_at" in valid_detail.columns:
            valid_with_time = valid_detail[valid_detail["detected_at"].notna()].copy()
            if not valid_with_time.empty:
                valid_with_time["hour"] = valid_with_time["detected_at"].dt.hour
                hourly_agg = valid_with_time.groupby("hour").agg(
                    trades=("final_ret_pct", "count"),
                    avg_ret=("final_ret_pct", "mean"),
                    total_ret=("final_ret_pct", "sum"),
                    win_rate=("final_ret_pct", lambda x: (x > 0).mean() * 100),
                ).reset_index()

                st.subheader("시간대별 통합 성과")
                col_h1, col_h2 = st.columns(2)
                with col_h1:
                    fig_ht = px.bar(hourly_agg, x="hour", y="total_ret",
                                    text=[f"{v:.1f}%" for v in hourly_agg["total_ret"]],
                                    title="시간대별 총 수익률",
                                    color="total_ret",
                                    color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                                    labels={"hour": "시간 (KST)", "total_ret": "총 수익률 (%)"})
                    fig_ht.update_layout(height=350)
                    st.plotly_chart(fig_ht, use_container_width=True)

                with col_h2:
                    fig_hw = px.scatter(hourly_agg, x="hour", y="win_rate", size="trades",
                                        title="시간대별 승률 (크기=매매수)",
                                        color="avg_ret",
                                        color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                                        labels={"hour": "시간 (KST)", "win_rate": "승률 (%)"})
                    fig_hw.add_hline(y=50, line_dash="dash", line_color="gray")
                    fig_hw.update_layout(height=350)
                    st.plotly_chart(fig_hw, use_container_width=True)

        # 키워드 통합 성과
        if "keyword_hits" in valid_detail.columns:
            kw_rows = []
            for _, row in valid_detail.iterrows():
                hits = row.get("keyword_hits")
                if isinstance(hits, list):
                    for kw in hits:
                        kw_rows.append({"keyword": kw, "ret": row["final_ret_pct"]})
            if kw_rows:
                kw_agg = pd.DataFrame(kw_rows)
                kw_stats = kw_agg.groupby("keyword").agg(
                    trades=("ret", "count"),
                    avg_ret=("ret", "mean"),
                    total_ret=("ret", "sum"),
                    win_rate=("ret", lambda x: (x > 0).mean() * 100),
                ).reset_index().sort_values("total_ret", ascending=False)

                st.subheader("키워드 통합 성과 (멀티데이)")
                col_k1, col_k2 = st.columns(2)
                with col_k1:
                    top15 = kw_stats.head(15)
                    fig_kw_m = px.bar(top15, x="keyword", y="total_ret",
                                      color="win_rate", text=[f"{v:.1f}%" for v in top15["total_ret"]],
                                      color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                                      range_color=[0, 100],
                                      title="키워드별 총 수익률 Top 15 (색상=승률)")
                    fig_kw_m.update_layout(height=450)
                    st.plotly_chart(fig_kw_m, use_container_width=True)

                with col_k2:
                    kw_display = kw_stats.copy()
                    kw_display.columns = ["키워드", "매매수", "평균수익률(%)", "총수익률(%)", "승률(%)"]
                    for c in ["평균수익률(%)", "총수익률(%)", "승률(%)"]:
                        kw_display[c] = kw_display[c].round(2)
                    st.dataframe(kw_display, use_container_width=True, hide_index=True, height=450)

        # 이벤트 타임라인 (당일)
        st.divider()
        st.subheader("이벤트 타임라인 (당일)")
        if not events_df.empty and "detected_at" in events_df.columns:
            timeline = events_df[events_df["detected_at"].notna()].copy()
            timeline["hour_min"] = timeline["detected_at"].dt.strftime("%H:%M")
            timeline["action_label"] = timeline["decision_action"].fillna(
                timeline.get("skip_stage", pd.Series(["FILTERED"] * len(timeline)))
            )
            fig_tl = px.scatter(
                timeline, x="detected_at", y="bucket",
                color="action_label",
                hover_data=["ticker", "corp_name", "headline"],
                title=f"이벤트 타임라인 — {selected_date[:4]}-{selected_date[4:6]}-{selected_date[6:]}",
                color_discrete_map={"BUY": "#2ecc71", "SKIP": "#e74c3c",
                                    "BUCKET": "#95a5a6", "DUPLICATE": "#bdc3c7",
                                    "QUANT": "#f39c12"},
                labels={"detected_at": "시각", "bucket": "버킷", "action_label": "결과"},
            )
            fig_tl.update_layout(height=400)
            st.plotly_chart(fig_tl, use_container_width=True)

        # ── 전략 최적화 제안 ──
        if not valid_detail.empty and total_t >= 3:
            st.divider()
            st.subheader("전략 최적화 제안")
            suggestions = []

            # 1. 최적 Confidence 임계값 탐색
            conf_numeric = pd.to_numeric(valid_detail["confidence"], errors="coerce").dropna()
            if len(conf_numeric) >= 3:
                best_thresh, best_profit = 78, 0.0
                for thresh in range(70, 95):
                    above = valid_detail[pd.to_numeric(valid_detail["confidence"], errors="coerce") >= thresh]
                    if len(above) >= 2:
                        profit = above["final_ret_pct"].sum()
                        if profit > best_profit:
                            best_profit = profit
                            best_thresh = thresh
                current_thresh = 78
                if best_thresh != current_thresh:
                    suggestions.append({
                        "항목": "Confidence 임계값",
                        "현재": f"{current_thresh}",
                        "제안": f"{best_thresh}",
                        "근거": f"총수익률 {best_profit:.2f}% (현재 기준 대비 최적)",
                        "영향": "높음",
                    })

            # 2. 위험 시간대 식별
            if "detected_at" in valid_detail.columns:
                vt = valid_detail[valid_detail["detected_at"].notna()].copy()
                if not vt.empty:
                    vt["hour"] = vt["detected_at"].dt.hour
                    for h in vt["hour"].unique():
                        h_data = vt[vt["hour"] == h]
                        if len(h_data) >= 2:
                            h_wr = (h_data["final_ret_pct"] > 0).mean() * 100
                            h_avg = h_data["final_ret_pct"].mean()
                            if h_wr < 30 and h_avg < -0.3:
                                suggestions.append({
                                    "항목": f"{int(h)}시 매매 제한",
                                    "현재": "허용",
                                    "제안": "제한 검토",
                                    "근거": f"승률 {h_wr:.0f}%, 평균 {h_avg:.2f}% (n={len(h_data)})",
                                    "영향": "중간",
                                })

            # 3. 수익 키워드 미등록 감지 (UNKNOWN에서 수익 낸 키워드)
            if "keyword_hits" in valid_detail.columns:
                unknown_wins = valid_detail[
                    (valid_detail["bucket"] == "UNKNOWN") & (valid_detail["final_ret_pct"] > 0.5)
                ]
                if not unknown_wins.empty:
                    suggestions.append({
                        "항목": "UNKNOWN 버킷 수익 종목",
                        "현재": f"{len(unknown_wins)}건 미분류",
                        "제안": "POS 버킷 키워드 등록 검토",
                        "근거": f"UNKNOWN에서 +0.5% 이상 수익 {len(unknown_wins)}건",
                        "영향": "높음",
                    })

            # 4. 손실 버킷 경고
            bucket_perf_check = valid_detail.groupby("bucket")["final_ret_pct"].agg(["mean", "count", "sum"])
            for b, row in bucket_perf_check.iterrows():
                if row["count"] >= 3 and row["sum"] < -2.0:
                    suggestions.append({
                        "항목": f"버킷 {b} 손실 누적",
                        "현재": "매매 허용",
                        "제안": "버킷 필터 강화 검토",
                        "근거": f"총손실 {row['sum']:.2f}%, 평균 {row['mean']:.2f}% (n={int(row['count'])})",
                        "영향": "높음",
                    })

            # 5. Size Hint 최적화
            if "size_hint" in valid_detail.columns:
                for sh in valid_detail["size_hint"].dropna().unique():
                    sh_data = valid_detail[valid_detail["size_hint"] == sh]
                    if len(sh_data) >= 3:
                        sh_wr = (sh_data["final_ret_pct"] > 0).mean() * 100
                        sh_avg = sh_data["final_ret_pct"].mean()
                        if sh == "L" and sh_wr < 40:
                            suggestions.append({
                                "항목": "Large 포지션 승률 저조",
                                "현재": f"L 사이즈 허용",
                                "제안": "M으로 하향 검토",
                                "근거": f"승률 {sh_wr:.0f}%, 평균 {sh_avg:.2f}% (n={len(sh_data)})",
                                "영향": "중간",
                            })

            if suggestions:
                sug_df = pd.DataFrame(suggestions)
                st.dataframe(sug_df, use_container_width=True, hide_index=True)
            else:
                st.success("현재 설정에서 특별한 최적화 제안 없음. 전략이 안정적입니다.")


# ── 푸터 ──────────────────────────────────────────────
st.sidebar.divider()
st.sidebar.caption("Kindshot Trading Dashboard v1.0")
st.sidebar.caption("streamlit run dashboard/app.py")
