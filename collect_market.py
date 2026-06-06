import gspread
from google.oauth2.service_account import Credentials
import yfinance as yf
from datetime import datetime, timezone, timedelta
import os

CREDENTIALS_FILE = os.environ.get('CREDENTIALS_FILE', 'credentials.json')
SHEET_ID = os.environ.get('SHEET_ID', '')

JST = timezone(timedelta(hours=9))

WATCH: list[tuple[str, str, str]] = [
    # 注目個別銘柄
    ('BE',     '注目銘柄', 'Bloom Energy'),
    ('ARM',    '注目銘柄', 'ARM'),
    ('RKLB',   '注目銘柄', 'Rocket Lab'),
    ('7777.T', '注目銘柄', '3Dマトリックス'),
    ('186A.T', '注目銘柄', 'アストロスケール'),
    # 業界ETF
    ('BOTZ',   'AI',       'AI・ロボ(BOTZ)'),
    ('QTUM',   '量子',     '量子コンピュータ(QTUM)'),
    ('SOXX',   '半導体',   '半導体(SOXX)'),
    ('UFO',    '宇宙',     '宇宙(UFO)'),
]

YIELD_TICKERS = {'^TNX', '^TYX', '^IRX'}


def _fetch_df():
    tickers = [t for t, _, _ in WATCH]
    return yf.download(tickers, period='5d', auto_adjust=True, progress=False)['Close']


def _fmt_value(ticker: str, latest: float, prev: float) -> str:
    chg_pct = (latest - prev) / prev * 100
    if ticker in YIELD_TICKERS:
        return f'{latest:.3f}%  ({chg_pct:+.2f}%)'
    if latest >= 100:
        return f'{latest:,.2f}  ({chg_pct:+.2f}%)'
    return f'{latest:.4f}  ({chg_pct:+.2f}%)'


# ── 公開 API ─────────────────────────────────────────────────────────────────

def format_market_summary() -> str:
    """メール本文に挿入できる市場概況セクションを返す。

    Returns:
        改行区切りのテキスト。データ取得失敗時は空文字列。
    """
    try:
        df = _fetch_df()
    except Exception as e:
        print(f'[WARN] 市場データ取得失敗: {e}')
        return ''

    # type ごとにグループ化して見やすく整形
    sections: dict[str, list[str]] = {}
    for ticker, type_, name in WATCH:
        col = df[ticker].dropna()
        if len(col) < 2:
            continue
        latest, prev = float(col.iloc[-1]), float(col.iloc[-2])
        trading_date = col.index[-1].strftime('%m/%d')
        line = f'  {name:<12s} {_fmt_value(ticker, latest, prev)}  [{trading_date}]'
        sections.setdefault(type_, []).append(line)

    if not sections:
        return ''

    parts = ['【注目銘柄・業界ETF】']
    for type_, lines in sections.items():
        parts.append(f'▼{type_}')
        parts.extend(lines)

    return '\n'.join(parts)


# ── Sheets 保存（CLI 実行時のみ）────────────────────────────────────────────

def collect() -> list[list]:
    df = _fetch_df()
    now = datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    rows = []
    for ticker, type_, name in WATCH:
        col = df[ticker].dropna()
        if len(col) < 2:
            print(f'[SKIP] {name}: データ不足')
            continue
        latest, prev = float(col.iloc[-1]), float(col.iloc[-2])
        trading_date = col.index[-1].strftime('%Y/%m/%d')
        value = f'{trading_date}  {_fmt_value(ticker, latest, prev)}'
        rows.append([now, type_, name, value, '', ''])
        print(f'{type_:6s} | {name:12s} | {value}')
    return rows


if __name__ == '__main__':
    scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).sheet1

    rows = collect()
    if rows:
        sheet.append_rows(rows)
        print(f'{len(rows)}件追加しました')
    else:
        print('保存データなし')
