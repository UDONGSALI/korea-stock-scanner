# Korea Stock Trend Scanner

KOSPI/KOSDAQ 전 종목을 대상으로 깡토의 공개 추세추종 원칙과 StockEasy 공개 설명에서 객관적으로 재현 가능한 조건을 자동 계산합니다.

## 현재 규칙: `kangto_core_v3`

현재 스캐너는 한 번에 BUY만 찾지 않고 아래 단계로 나눠 봅니다.

`EARLY LEADER → LEADER → SETUP → BUY`

### 1. 시장 상태

- KOSPI: KOSPI 종가가 60일 이동평균 위인지 확인
- KOSDAQ: KOSDAQ 종가가 120일 이동평균 위인지 확인

시장 상태는 BUY를 차단하지 않습니다.

- 시장 조건 PASS인 BUY: `WITH_MARKET`
- 시장 조건 FAIL인 BUY: `COUNTERTREND`

둘 다 `signals`와 `captures.csv`에 정식 BUY로 들어가며 표시만 다르게 합니다.

### 2. 거래 유니버스

- 시가총액 2,000억원 이상

### 3. EARLY LEADER

새로운 주도주를 늦지 않게 발견하기 위한 단계입니다.

- 종가 > SMA50
- 52주 고점에서 30% 이내
- 아래 중 하나 이상 충족
  - 1개월 RS 백분위 80 이상
  - 3개월 RS 백분위 80 이상
  - 종합 RS가 최근 5거래일 동안 10점 이상 상승

종합 RS가 아직 70을 넘지 않았더라도 최근 시장 대비 강도가 빠르게 올라오는 종목을 먼저 보여줍니다.

### 4. EARLY 이후 상태

최초 `EARLY_LEADER` 포착 이후 종목이 얼마나 진행됐는지를 별도 상태로 표시합니다.

- `FRESH`: EARLY 이후 상승률 15% 미만
- `NORMAL`: 15% 이상 30% 미만
- `LATE`: 30% 이상 45% 미만
- `CLIMAX_RISK`: 45% 이상 상승했거나, 15% 이상 오른 상태에서 RS 가속이 매우 강한 경우
- `NO_EARLY`: 저장된 EARLY 기록 없음
- `PRE_EARLY`: 과거 재스캔에서 최초 EARLY 이전 시점

각 EARLY LEADER / LEADER / SETUP / BUY 결과에 다음 값을 함께 저장합니다.

- `first_early_date`
- `first_early_close`
- `gain_since_early_pct`
- `trading_days_since_early`
- `early_state`
- `early_state_reason`

이 값은 종목의 좋고 나쁨 자체보다는 `신선한 진입인지`, `이미 많이 진행된 진입인지`를 구분하기 위한 성숙도 표시입니다.

### 5. LEADER

- 종가 > SMA50
- 52주 고점에서 25% 이내
- 종합 `RS Score >= 70`

종합 RS는 1M(20일), 3M(60일), 6M(120일), 12M(240일) 시장 대비 상대수익률을 동일 가중해 전체 KOSPI/KOSDAQ 종목 백분위로 계산합니다.

StockEasy의 실제 종합 RS 가중치는 공개되지 않았기 때문에 동일 가중 방식은 공개 데이터로 재현하기 위한 프록시입니다.

### 6. MTT

Mark Minervini Trend Template은 더 이상 필수 탈락 조건이 아닙니다.

결과에 `mtt: true/false`로 표시해 장기 추세 품질을 확인하는 참고값으로 사용합니다.

### 7. 동적 Base / SETUP

고정 15거래일 전체를 Base로 취급하지 않습니다.

직전 5일, 7일, 10일, 15일, 20일 구간을 각각 계산한 뒤 고점 대비 낙폭이 20% 이내인 가장 긴 구간을 현재 Base로 선택합니다.

- Base 깊이 = `(Base 고점 - Base 저점) / Base 고점`
- 최근 3일 평균 거래량 <= 직전 20일 평균 거래량: 조정 중 거래량 수축
- 종가가 Base 상단 3% 이내까지 접근하면 `SETUP`

이 방식은 이전 상승 전 저점까지 Base에 포함돼 강한 종목이 과도하게 탈락하는 문제를 줄이기 위한 공개 재현용 방식입니다.

### 8. BUY

EARLY LEADER 또는 LEADER가 다음을 만족하면 BUY입니다.

- 유효 Base 존재
- Base 구간 거래량 수축
- 종가가 Base 상단 돌파
- 돌파일 거래량 >= 직전 20일 평균 거래량
- 종가가 Base 상단을 10% 넘게 과도하게 이격하지 않음

시장 상태는 BUY 여부에 영향을 주지 않고 `WITH_MARKET` / `COUNTERTREND` 라벨만 붙습니다.

### 9. 리스크 관리 출력

BUY가 발생하면 Base 저점을 구조적 손절선으로 두고 다음 값을 함께 계산합니다.

- 손절 거리
- 계좌 최대 위험 2% 기준 최대 포지션 비중
- 3R 목표 가격

이는 매수 신호 필터가 아니라 포지션 사이징 참고값입니다.

## 자동화하지 않는 확인 항목

현재 KRX OHLCV만으로 정확하게 재현하기 어려운 항목은 결과 JSON의 `manual_checks`에 남깁니다.

- 주도 섹터 / 섹터 RS
- 인더스트리 액션
- 기관 수급
- EPS 성장 및 실적

임의의 가짜 점수로 대체하지 않습니다.

## 데이터

- `data/snapshots/`: KOSPI/KOSDAQ 전 종목 일별 OHLCV 원본
- `data/latest.json`: 최신 EARLY LEADER / LEADER / SETUP / BUY 및 EARLY 이후 상태
- `data/results/`: 날짜별 결과
- `data/history_signals.json`: 과거 BUY 및 단계 진입 이벤트
- `data/early_registry.csv`: 종목별 최초 EARLY 포착 날짜/가격
- `data/captures.csv`: 현재 규칙의 모든 BUY 자동 누적
- `data/tracking.json`: BUY 포착 이후 성과

## 실행

```bash
pip install -r requirements.txt
python src/scanner.py
python src/early_state.py
```

과거 재스캔:

```bash
python src/history_scan.py --from-date 2026-08-01 --to-date 2026-08-27
```

## GitHub Actions

월~금 한국시간 18:37 자동 실행되며 수동 실행도 가능합니다.

Repository Settings > Secrets and variables > Actions에 다음 값을 등록합니다.

- `KRX_ID`
- `KRX_PW`
