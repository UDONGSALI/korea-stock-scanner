from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from statistics import mean, median

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_exit_rules as exit_rules

root_dir = Path(__file__).resolve().parents[1]
data_dir = root_dir / 'data'
out_dir = data_dir / 'pullback_analysis_20261007'
end_date = '2026-10-07'
source_commit = 'a8e1f9ee83d903eb3a17c404c6b206121fd55581'
unit_krw = 6_250_000


def isNumber(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def summarizeRows(rows: list[dict]) -> dict:
    filled = [r for r in rows if r['filled']]
    returns = [r['return_pct'] for r in rows]
    fill_returns = [r['return_pct'] for r in filled]
    wins = [x for x in fill_returns if x > 1e-9]
    losses = [x for x in fill_returns if x < -1e-9]
    closed = [r for r in filled if r['trade_status'] == 'CLOSED']
    missing = [r for r in rows if not r['filled']]
    stopped = [r for r in filled if str(r['exit_reason']).startswith('STOP_LOSS')]
    status_counts = {}
    for row in rows:
        status = row['order_status'] if not row['filled'] else row['trade_status']
        status_counts[status] = status_counts.get(status, 0) + 1
    baseline = [r['baseline_return_pct'] for r in rows]
    opportunity_index = [r['index_hold_return_pct'] for r in rows]
    return {
        'signals': len(rows), 'filled': len(filled), 'fill_rate_pct': 100 * len(filled) / len(rows),
        'mean_filled_return_pct': mean(fill_returns) if filled else None,
        'mean_signal_return_pct': mean(returns), 'median_filled_return_pct': median(fill_returns) if filled else None,
        'win_rate_filled_pct': 100 * len(wins) / len(filled) if filled else None,
        'winners': len(wins), 'losers': len(losses), 'flat': len(filled) - len(wins) - len(losses),
        'avg_winner_pct': mean(wins) if wins else None, 'avg_loser_pct': mean(losses) if losses else None,
        'profit_factor_marked': sum(wins) / -sum(losses) if losses else None,
        'stops': len(stopped), 'stop_rate_filled_pct': 100 * len(stopped) / len(filled) if filled else None,
        'avg_stop_return_pct': mean(r['return_pct'] for r in stopped) if stopped else None,
        'closed_count': len(closed), 'closed_mean_return_pct': mean(r['return_pct'] for r in closed) if closed else None,
        'marked_pnl_krw': round(sum(returns) * unit_krw / 100),
        'allocated_capital_krw': len(rows) * unit_krw,
        'gross_entry_notional_krw': len(filled) * unit_krw,
        'baseline_signal_mean_pct': mean(baseline),
        'improvement_vs_baseline_pp': mean(returns) - mean(baseline),
        'mean_buy_index_at_signal_hold_pct': mean(opportunity_index),
        'excess_vs_index_at_signal_pp': mean(returns) - mean(opportunity_index),
        'unfilled_baseline_mean_pct': mean(r['baseline_return_pct'] for r in missing) if missing else None,
        'unfilled_baseline_winners': sum(r['baseline_return_pct'] > 0 for r in missing),
        'unfilled_baseline_large_winners_20': sum(r['baseline_return_pct'] >= 20 for r in missing),
        'baseline_large_winners_20': sum(r['baseline_return_pct'] >= 20 for r in rows),
        'filled_baseline_mean_pct': mean(r['baseline_return_pct'] for r in filled) if filled else None,
        'avg_wait_trading_days': mean(r['wait_days'] for r in filled) if filled else None,
        'entry_gap_count': sum(r['entry_kind'] == 'OPEN_BETTER_THAN_LIMIT' for r in filled),
        'ambiguous_entry_day_target_count': sum(r['ambiguous_entry_day_target'] for r in rows),
        'status_counts': status_counts,
    }


def modelTrade(capture: dict, bars: list[dict], date_positions: dict, config: dict, dip: float | None,
               stop_mode: str = 'SIGNAL', target_mode: str = 'KEEP_LEVELS', expiry: int | None = None,
               ambiguity: str = 'CONSERVATIVE', gap_mode: str = 'CANCEL') -> dict:
    signal_date = str(capture['capture_date'])
    signal_price = float(capture['capture_close'])
    original_stop = signal_price * .92
    limit_price = signal_price * (1 - (dip or 0) / 100)
    future = [r for r in bars if r['date_text'] > signal_date]
    entry_bar = None
    entry_price = None
    entry_kind = None
    order_status = 'NOT_REACHED'
    entry_wait = None
    instant_stop = False
    if dip is None:
        entry_price = signal_price
        entry_date = signal_date
        entry_kind = 'SIGNAL_CLOSE_ASSUMPTION'
        entry_wait = 0
        trade_bars = future
    else:
        if not future:
            order_status = 'NO_FUTURE_BAR'
        for row in future:
            wait_days = date_positions[row['date_text']] - date_positions[signal_date]
            if expiry is not None and wait_days > expiry:
                order_status = 'EXPIRED'
                break
            if min(row['open'], row['low'], row['close']) <= 0:
                continue
            if row['low'] > limit_price:
                continue
            # This is a conditional-entry policy, not an assertion that an already-live
            # auction limit order can be cancelled after its execution.
            if stop_mode == 'SIGNAL' and row['open'] <= original_stop:
                if gap_mode == 'CANCEL':
                    order_status = 'CANCELLED_STOP_GAP'
                    break
                instant_stop = True
            entry_bar = row
            entry_date = row['date_text']
            entry_price = min(row['open'], limit_price)
            entry_kind = 'OPEN_BETTER_THAN_LIMIT' if row['open'] <= limit_price else 'INTRADAY_LIMIT'
            entry_wait = wait_days
            trade_bars = [r for r in future if r['date_text'] >= entry_date]
            break
        if entry_bar is None and order_status == 'NOT_REACHED' and expiry is not None:
            available_days = date_positions[end_date] - date_positions[signal_date]
            if available_days >= expiry:
                order_status = 'EXPIRED'
    output = {
        'capture_date': signal_date, 'ticker': str(capture['ticker']), 'name': capture['name'],
        'signal_price': signal_price, 'limit_price': limit_price, 'filled': entry_price is not None,
        'order_status': 'FILLED' if entry_price is not None else order_status,
        'entry_date': None, 'entry_price': entry_price, 'entry_kind': entry_kind, 'wait_days': entry_wait,
        'initial_stop': None, 'target_price': None, 'trade_status': None,
        'partial_exit_date': None, 'partial_exit_price': None, 'exit_date': None, 'exit_price': None,
        'exit_reason': None, 'remaining_pct': None, 'return_pct': 0.0,
        'ambiguous_entry_day_target': False, 'realized_return_pct': 0.0,
    }
    if entry_price is None:
        return output
    initial_stop = original_stop if stop_mode == 'SIGNAL' else entry_price * .92
    # KEEP_LEVELS isolates the entry price: retain all original target/BE/pace levels.
    # ENTRY_3R reanchors target, breakeven and pace to the actual entry and actual risk.
    anchor_price = signal_price if target_mode == 'KEEP_LEVELS' else entry_price
    risk_per_share = (signal_price - original_stop) if target_mode == 'KEEP_LEVELS' else (entry_price - initial_stop)
    target_price = anchor_price + risk_per_share * 3
    current_stop = initial_stop
    highest_close = anchor_price
    remaining = 1.0
    partial_date = partial_price = exit_date = exit_price = exit_reason = None
    trailing_basis = 'INITIAL'
    ambiguous = False
    if instant_stop:
        exit_date, exit_price, exit_reason = entry_date, entry_price, 'ENTRY_AND_STOP_AT_OPEN'
        remaining = 0.0
    else:
        for row in trade_bars:
            row_date = row['date_text']
            opened, high, close = row['open'], row['high'], row['close']
            highest_close = max(highest_close, close)
            entry_day = dip is not None and row_date == entry_date
            if partial_date is None:
                if not entry_day and opened < initial_stop:
                    exit_date, exit_price, exit_reason = row_date, opened, 'STOP_LOSS_GAP'
                    remaining = 0.0
                    break
                target_hit = high >= target_price
                if entry_day and entry_kind == 'INTRADAY_LIMIT' and target_hit and close < target_price:
                    ambiguous = True
                    target_hit = ambiguity == 'OPTIMISTIC'
                if target_hit:
                    partial_date, partial_price = row_date, target_price
                    remaining = .5
                    current_stop = max(current_stop, anchor_price)
                    trailing_basis = 'BE'
                    if close <= current_stop:
                        exit_date, exit_price, exit_reason = row_date, close, 'BREAKEVEN_EXIT'
                        remaining = 0.0
                        break
                    continue
                if close <= initial_stop:
                    exit_date, exit_price, exit_reason = row_date, close, 'STOP_LOSS'
                    remaining = 0.0
                    break
                continue
            if opened < current_stop:
                exit_date, exit_price = row_date, opened
                exit_reason = f'TREND_EXIT_GAP_{trailing_basis}' if trailing_basis != 'BE' else 'BREAKEVEN_GAP_EXIT'
                remaining = 0.0
                break
            peak_r = (highest_close - anchor_price) / risk_per_share if risk_per_share > 0 else 0.0
            if exit_rules.is_climax_exit(row, peak_r, config['exit']):
                exit_date, exit_price, exit_reason = row_date, close, 'CLIMAX_EXIT'
                remaining = 0.0
                break
            _, ma_days = exit_rules.get_trend_pace(highest_close, anchor_price, risk_per_share, config['exit'])
            ma_value = row.get(f'sma{ma_days}_exit')
            if isNumber(ma_value):
                current_stop = max(current_stop, anchor_price, float(ma_value))
                trailing_basis = f'{ma_days}MA'
            else:
                current_stop = max(current_stop, anchor_price)
                trailing_basis = 'BE'
            if close <= current_stop:
                exit_date, exit_price = row_date, close
                exit_reason = f'TREND_EXIT_{trailing_basis}' if trailing_basis != 'BE' else 'BREAKEVEN_EXIT'
                remaining = 0.0
                break
    marked_price = exit_price if exit_price is not None else bars[-1]['close']
    final_return = (marked_price / entry_price - 1) * 100
    partial_return = (partial_price / entry_price - 1) * 100 if partial_price is not None else None
    return_pct = final_return if partial_return is None else .5 * partial_return + .5 * final_return
    realized_return = return_pct if exit_price is not None else (partial_return * .5 if partial_return is not None else 0)
    output.update({
        'entry_date': entry_date, 'initial_stop': initial_stop, 'target_price': target_price,
        'trade_status': 'CLOSED' if exit_date else ('PARTIAL' if partial_date else 'OPEN'),
        'partial_exit_date': partial_date, 'partial_exit_price': partial_price, 'exit_date': exit_date,
        'exit_price': exit_price, 'exit_reason': exit_reason, 'remaining_pct': remaining * 100,
        'return_pct': return_pct, 'realized_return_pct': realized_return,
        'ambiguous_entry_day_target': ambiguous,
    })
    return output


def main() -> None:
    config = json.loads((root_dir / 'config.json').read_text(encoding='utf-8-sig'))
    with (data_dir / 'captures.csv').open(encoding='utf-8-sig', newline='') as file:
        captures = list(csv.DictReader(file))
    with (data_dir / 'all_buy_vs_index_20261007.csv').open(encoding='utf-8-sig', newline='') as file:
        prior = list(csv.DictReader(file))
    tracking = json.loads((data_dir / 'tracking.json').read_text(encoding='utf-8-sig'))
    assert len(captures) == len(prior) == len(tracking) == 137
    assert max(row['capture_date'] for row in captures) == end_date
    prior_map = {(r['capture_date'], r['ticker']): r for r in prior}
    history = exit_rules.add_exit_indicators(exit_rules.load_price_history({r['ticker'] for r in captures}), config)
    history = history[history['date'] <= pd.Timestamp(end_date)].copy()
    duplicates = int(history.duplicated(['ticker', 'date']).sum())
    assert duplicates == 0, f'duplicate OHLC keys: {duplicates}'
    history['date_text'] = history['date'].dt.strftime('%Y-%m-%d')
    bars_by_ticker = {ticker: frame.to_dict('records') for ticker, frame in history.groupby('ticker')}
    date_positions = {date: i for i, date in enumerate(sorted(history['date_text'].unique()))}
    invalid_ohlc = history[(history['low'] > history[['open', 'close', 'high']].min(axis=1)) | (history['high'] < history[['open', 'close', 'low']].max(axis=1))]
    assert invalid_ohlc.empty, f'invalid OHLC: {len(invalid_ohlc)}'
    baseline_rows = []
    baseline_errors = []
    capture_price_errors = []
    for capture in captures:
        bars = bars_by_ticker[capture['ticker']]
        stored = prior_map[(capture['capture_date'], capture['ticker'])]
        actual_signal = next((bar for bar in bars if bar['date_text'] == capture['capture_date']), None)
        if actual_signal is None or abs(actual_signal['close'] - float(capture['capture_close'])) > .01:
            capture_price_errors.append({'ticker': capture['ticker'], 'date': capture['capture_date']})
        row = modelTrade(capture, bars, date_positions, config, None)
        row.update({'market': stored['market'], 'baseline_return_pct': float(stored['strategy_return_pct']), 'index_hold_return_pct': float(stored['current_benchmark_return_pct'])})
        if abs(row['return_pct'] - row['baseline_return_pct']) > 1e-7:
            baseline_errors.append({'ticker': capture['ticker'], 'date': capture['capture_date'], 'replay': row['return_pct'], 'stored': row['baseline_return_pct']})
        baseline_rows.append(row)
    assert not baseline_errors, f'baseline replay mismatch: {baseline_errors[:10]}'
    assert not capture_price_errors, f'signal price mismatch: {capture_price_errors[:10]}'
    scenarios = [('A_SIGNAL_CLOSE', None, 'SIGNAL', 'KEEP_LEVELS', None, 'CONSERVATIVE', 'CANCEL')]
    for dip in [2, 3, 4, 5]:
        for expiry in [None, 5]:
            scenarios.append((f'B_DIP{dip}_KEEP_{expiry or "GTC"}', dip, 'SIGNAL', 'KEEP_LEVELS', expiry, 'CONSERVATIVE', 'CANCEL'))
    for expiry in [1, 3, 10]:
        scenarios.append((f'B_DIP4_KEEP_{expiry}', 4, 'SIGNAL', 'KEEP_LEVELS', expiry, 'CONSERVATIVE', 'CANCEL'))
    for expiry in [None, 5]:
        scenarios.append((f'B_DIP4_3R_{expiry or "GTC"}', 4, 'SIGNAL', 'ENTRY_3R', expiry, 'CONSERVATIVE', 'CANCEL'))
        scenarios.append((f'C_DIP4_STOPFILL_{expiry or "GTC"}', 4, 'FILL', 'ENTRY_3R', expiry, 'CONSERVATIVE', 'CANCEL'))
    scenarios += [('B_DIP4_KEEP_GTC_OPTIMISTIC', 4, 'SIGNAL', 'KEEP_LEVELS', None, 'OPTIMISTIC', 'CANCEL'), ('B_DIP4_3R_GTC_OPTIMISTIC', 4, 'SIGNAL', 'ENTRY_3R', None, 'OPTIMISTIC', 'CANCEL'), ('B_DIP4_KEEP_GTC_GAPFILL', 4, 'SIGNAL', 'KEEP_LEVELS', None, 'CONSERVATIVE', 'FILL')]
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    all_rows = []
    detail_map = {}
    for name, dip, stop_mode, target_mode, expiry, ambiguity, gap_mode in scenarios:
        output_rows = []
        for capture in captures:
            row = modelTrade(capture, bars_by_ticker[capture['ticker']], date_positions, config, dip, stop_mode, target_mode, expiry, ambiguity, gap_mode)
            stored = prior_map[(capture['capture_date'], capture['ticker'])]
            row.update({'scenario': name, 'market': stored['market'], 'baseline_return_pct': float(stored['strategy_return_pct']), 'index_hold_return_pct': float(stored['current_benchmark_return_pct'])})
            output_rows.append(row)
        detail_map[name] = output_rows
        all_rows.extend(output_rows)
        for cohort, start in [('ALL_137', '2026-08-24'), ('SINCE_0911_65', '2026-09-11')]:
            subset = [r for r in output_rows if r['capture_date'] >= start]
            summary_rows.append({'scenario': name, 'cohort': cohort, 'dip_pct': dip, 'stop_mode': stop_mode, 'target_mode': target_mode, 'expiry_trading_days': expiry, **summarizeRows(subset)})
    winners = []
    worst = []
    for index, baseline_row in enumerate(baseline_rows):
        if baseline_row['baseline_return_pct'] >= 12 or baseline_row['baseline_return_pct'] <= -11:
            item = {k: baseline_row[k] for k in ['capture_date', 'ticker', 'name', 'baseline_return_pct']}
            item['variants'] = {key: {k: detail_map[key][index][k] for k in ['filled', 'order_status', 'entry_date', 'wait_days', 'entry_price', 'exit_date', 'exit_reason', 'return_pct']} for key in ['B_DIP4_KEEP_GTC', 'B_DIP4_KEEP_5', 'B_DIP4_3R_GTC', 'C_DIP4_STOPFILL_GTC']}
            (winners if baseline_row['baseline_return_pct'] >= 12 else worst).append(item)
    report = {
        'source_commit': source_commit, 'period_end': end_date, 'unit_krw': unit_krw,
        'validation': {'baseline_reproduced': 137, 'baseline_max_abs_error': max(abs(r['return_pct'] - r['baseline_return_pct']) for r in baseline_rows), 'duplicate_ohlc': duplicates, 'invalid_ohlc': len(invalid_ohlc), 'capture_price_errors': len(capture_price_errors)},
        'method': {'entry': 'signal next trading day or later; open <= limit fills at open, else daily low <= limit fills at limit', 'stop': 'close confirmation, gap below active stop uses open; entry-day close stop included', 'gap_under_initial_stop': 'main case conditional cancel; separate standing-order gap-fill sensitivity', 'KEEP_LEVELS': 'same original signal*0.92 stop, signal*1.24 target, original signal breakeven and trend pace references', 'ENTRY_3R': 'target=actual_entry+3*(actual_entry-stop), breakeven=actual_entry', 'capital': 'same fixed notional per signal; missing entry=0%; no leverage sizing increase; each capture independent', 'costs': 'fees/taxes/slippage/tick rounding/queue depth excluded', 'GTC': 'wait through evaluation date; no arbitrary signal expiry', 'benchmark': 'each signal buys its market index at signal close and holds to evaluation date; NOT the prior exit-date-matched benchmark'},
        'summary': summary_rows, 'large_winner_cases': winners, 'large_loss_cases': worst,
    }
    (out_dir / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    for file_name, rows in [('trades.csv', all_rows), ('summary.csv', [{k: v for k, v in r.items() if k != 'status_counts'} for r in summary_rows]), ('dip4_main.csv', detail_map['B_DIP4_KEEP_GTC'])]:
        with (out_dir / file_name).open('w', encoding='utf-8-sig', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    concise = {**{k: v for k, v in report.items() if k in ['source_commit', 'period_end', 'validation', 'method']}, 'summary': [{k: r[k] for k in ['scenario', 'cohort', 'signals', 'filled', 'mean_filled_return_pct', 'mean_signal_return_pct', 'win_rate_filled_pct', 'stops', 'stop_rate_filled_pct', 'marked_pnl_krw', 'unfilled_baseline_large_winners_20', 'ambiguous_entry_day_target_count', 'status_counts']} for r in summary_rows]}
    print(json.dumps(concise, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
