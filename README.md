# Korea Stock Trend Scanner

KOSPI/KOSDAQ 전 종목 OHLCV를 pykrx로 수집하고, 추세추종 신규 BUY 조건을 계산한 뒤 JSON으로 저장합니다.

## BUY 조건

- 직전 20일 평균 거래량 100,000주 이상
- 당일 거래량 / 직전 20일 평균 거래량 >= 1.0
- 종가 > 직전 55거래일 최고가
- 종가 > SMA20 > SMA60 > SMA120
- 60일 종목 수익률 - KOSPI 60일 수익률 >= +10%p
- ATR14 < ATR50
- 전 거래일에는 전체 조건을 만족하지 않았고 오늘 처음 만족한 경우만 신규 BUY

## 결과 파일

- `data/latest.json`: 가장 최근 스캔 결과
- `data/results/YYYY-MM-DD.json`: 날짜별 스캔 결과
- `data/tracking.json`: 포착 종목 누적 성과
- `data/snapshots/YYYY-MM-DD.csv.gz`: 일별 KOSPI/KOSDAQ OHLCV 원본

## GitHub Actions

매주 월~금 한국시간 18:37에 자동 실행됩니다. 수동 실행도 가능합니다.

Repository Settings > Secrets and variables > Actions 에 다음 값을 등록하세요.

- `KRX_ID`
- `KRX_PW`

pykrx 1.2.8 이상은 위 환경변수를 이용한 KRX 로그인 세션을 지원합니다.

## 최초 실행

```bash
pip install -r requirements.txt
python src/scanner.py
```

최초 실행은 과거 스냅샷을 채우므로 이후 일일 실행보다 오래 걸립니다.

## ChatGPT에서 읽는 방법

공개 저장소라면 `data/latest.json`의 GitHub Raw URL을 직접 읽을 수 있습니다. 비공개 저장소라면 별도 인증 가능한 API나 GitHub 연결이 필요합니다.
