# Telegram 알림 설정 가이드

## 개요

Kindshot은 텔레그램을 통해 실시간 트레이딩 알림을 발송합니다.
미설정 시 에러 없이 알림만 건너뜁니다 (fail-open).

### 알림 종류

| 알림 | 트리거 | 설명 |
|------|--------|------|
| 🟢 BUY 신호 | 가드레일 통과 후 매수 | 종목, confidence, 사이즈, TP/SL 등 |
| 🔴/✅ SELL 신호 | 포지션 청산 | 수익률, PnL, exit type |
| ⚠️ 가드레일 블록 | 고confidence 매수 거부 | 거부 사유, shadow 분석 여부 |
| 📘 일일 요약 | 장 마감 후 1회 | 승률, PnL, 포지션 현황 |
| 📈/📉 장중 모니터링 | 30분 간격 (설정 가능) | 실시간 성과 지표 |
| 🔄 백필 결과 | 데이터 수집 완료 시 | 수집 상태, 누락 건수 |

---

## 1단계: 봇 생성

1. Telegram에서 [@BotFather](https://t.me/BotFather) 검색
2. `/newbot` 명령 → 봇 이름/유저네임 입력
3. **API 토큰** 복사 (형식: `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`)

## 2단계: Chat ID 확인

1. 생성한 봇과 대화 시작 (아무 메시지 전송)
2. 브라우저에서 접속:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
3. 응답 JSON에서 `"chat":{"id": 123456789}` 값 복사

> **그룹 채팅**: 봇을 그룹에 초대 후 동일 방법. 그룹 ID는 음수 (예: `-100123456789`)

## 3단계: 서버 환경변수 설정

```bash
# SSH 접속
ssh kindshot-server

# .env 파일 편집
cd /opt/kindshot
nano .env
```

`.env`에 추가:
```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID=987654321
```

서비스 재시작:
```bash
sudo systemctl restart kindshot
```

## 4단계: 발송 테스트

```bash
# 포맷만 확인 (네트워크 불필요)
python scripts/test_telegram.py --dry-run

# 네트워크 없이 send 경로 검증
python scripts/test_telegram.py --simulate-send

# 실제 발송 테스트
python scripts/test_telegram.py

# 특정 메시지만 테스트
python scripts/test_telegram.py --type buy
python scripts/test_telegram.py --type sell
python scripts/test_telegram.py --type daily
python scripts/test_telegram.py --type guardrail
```

---

## 설정 옵션 (config.py)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `intraday_monitor_enabled` | `True` | 장중 모니터링 on/off |
| `intraday_monitor_interval_s` | `1800` (30분) | 모니터링 발송 간격 |
| `intraday_monitor_min_trades` | `1` | 최소 N건 거래 후 발송 |

---

## 미설정 시 동작

- **메인 파이프라인**: `try_send_*()` 함수들이 credentials 없으면 `False` 반환, DEBUG 로그만 남김
- **일일 요약/장중 모니터링**: `telegram_configured()` 체크 후 루프 자체를 건너뜀
- **스크립트**: `--telegram` 플래그 사용 시 credentials 없으면 exit code 1로 종료

**트레이딩 로직에는 영향 없음** — 알림은 순수 부가기능입니다.

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 메시지 미수신 | 봇과 대화 시작 안함 | 봇에게 `/start` 전송 |
| 403 Forbidden | chat_id 불일치 | getUpdates로 재확인 |
| 그룹 미수신 | 봇 권한 부족 | 그룹에 봇 재초대 |
| 일일 요약 미수신 | 장중 거래 0건 | 정상 (거래 없으면 미발송) |

## 아키텍처

```
telegram_ops.py
├── telegram_configured()        # credentials 존재 여부
├── _telegram_target()           # (token, chat_id) 또는 None
├── send_telegram_message()      # urllib 기반 HTTP POST
├── format_buy_signal()          # 메시지 포맷팅
├── format_sell_signal()
├── format_high_conf_skip_signal()
├── format_daily_summary_signal()
├── format_intraday_update()
├── format_backfill_notification()
├── try_send_buy_signal()        # format + send (예외 안전)
├── try_send_sell_signal()
├── try_send_high_conf_skip()
├── try_send_daily_summary()
├── try_send_intraday_update()
└── DailySummaryNotifier          # 일 1회 발송 보장
```
