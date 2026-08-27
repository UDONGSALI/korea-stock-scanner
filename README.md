# Korea Stock Trend Scanner

KOSPI/KOSDAQ 전 종목을 대상으로 깡토의 공개 추세추종 원칙과 StockEasy 공개 설명에서 객관적으로 재현 가능한 조건을 자동 계산합니다.

## 현재 규칙: `kangto_core_v2`

### 1. 시장 필터

- KOSPI 종목: KOSPI 종가가 60일 이동평균 위
- KOSDAQ 종목: KOSDAQ 종가가 120일 이동평균 위

### 2. 거래 유니버스

- 시가총액 2,000억원 이상

### 3. RS

- 1M(20일), 3M(60일), 6M(120일), 12M(240일) 시장 대비 상대수익률을 계산
- 네 기간을 동일 가중해 전체 KOSPI/KOSDAQ 종목 백분위 `RS Score` 산출
- `RS Score >= 70`

StockEasy의 실제 종합 RS 가중치는 공개되지 않았기 때문에 동일 가중 방식은 공개 데이터로 재현하기 위한 프록시입니다.

### 4. MTT / 장기 상승추세

- 종가 > SMA150, SMA200
- SMA150 > SMA200
- SMA200이 20거래일 전보다 상승
- SMA50 > SMA150, SMA200
- 종가 > SMA50
- 종가가 52주 저점 대비 30% 이상 위
- 종가가 52주 고점에서 25% 이내
- SMA50 > SMA150을 10주/30주 주봉 상승추세의 보수적 일봉 프록시로 사용

### 5. 베이스와 돌파

- 직전 15거래일을 조정 Base로 정의
- Base 고점/저점 폭이 20% 이내
- 최근 5일 평균 거래량 <= 직전 20일 평균 거래량: 조정 중 거래량 수축
- 종가가 Base 상단을 돌파
- 돌파일 거래량 >= 직전 20일 평균 거래량

StockEasy의 실제 돌파 가격 계산식은 비공개이므로 15거래일 Base 상단을 공개 재현용 돌파선으로 사용합니다.

### 6. 리스크 관리 출력

신호가 발생하면 Base 저점을 구조적 손절선으로 두고 다음 값을 함께 계산합니다.

- 손절 거리
- 계좌 최대 위험 2%를 기준으로 한 최대 포지션 비중
- 3R 목표 가격

이는 매수 신호 필터가 아니라 포지션 사이징 참고값입니다.

## 자동화하지 않는 확인 항목

현재 KRX OHLCV만으로 정확하게 재현하기 어려운 다음 항목은 결과 JSON의 `manual_checks`에 남깁니다.

- 주도 섹터 / 섹터 RS
- 인더스트리 액션
- 기관 수급
- EPS 성장 및 실적

임의의 가짜 점수로 대체하지 않습니다.

## 제거된 이전 규칙

아래 규칙은 더 이상 BUY 판정에 사용하지 않습니다.

- 55거래일 신고가
- 60일 KOSPI 대비 +10%p
- `ATR14 < ATR50`
- `종가 > SMA20 > SMA60 > SMA120`
- 20일 평균거래량 10만주 고정 필터

## 데이터

- `data/snapshots/`: KOSPI/KOSDAQ 전 종목 일별 OHLCV 원본
- `data/latest.json`: 최신 `kangto_core_v2` 후보
- `data/results/`: 날짜별 후보
- `data/captures.csv`: 현재 규칙에서 발생한 후보 자동 누적
- `data/tracking.json`: 포착 이후 성과
- `data/legacy_captures.csv`: 과거 웹 기반 기록
- `data/legacy_v1_captures.csv`: 폐기된 이전 KRX 규칙 기록

## 실행

```bash
pip install -r requirements.txt
python src/scanner.py
```

과거 재스캔:

```bash
python src/history_scan.py --from-date 2026-08-17 --to-date 2026-08-27
```

## GitHub Actions

월~금 한국시간 18:37 자동 실행되며 수동 실행도 가능합니다.

Repository Settings > Secrets and variables > Actions에 다음 값을 등록합니다.

- `KRX_ID`
- `KRX_PW`
