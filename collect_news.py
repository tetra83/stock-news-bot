import gspread
from google.oauth2.service_account import Credentials
import yfinance as yf
from newsapi import NewsApiClient
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
import os
CREDENTIALS_FILE = os.environ.get('CREDENTIALS_FILE', 'credentials.json')
SHEET_ID = os.environ.get('SHEET_ID', '')
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY', '')

# Google Sheets接続
scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

rows = []
JST = timezone(timedelta(hours=9))
now = datetime.now(JST).strftime('%Y-%m-%d %H:%M')

# 株価取得
tickers = {
    '日経平均': '^N225',
    'ドル円': 'JPY=X',
    'S&P500': '^GSPC'
}

for name, symbol in tickers.items():
    try:
        ticker = yf.Ticker(symbol)
        price = ticker.fast_info['last_price']
        rows.append([now, '株価', name, str(round(price, 2)), '', ''])
    except Exception as e:
        print(f'{name} 取得失敗: {e}')

# ニュース取得
try:
    newsapi = NewsApiClient(api_key=NEWSAPI_KEY)
    articles = newsapi.get_everything(
        q='Japan economy OR semiconductor OR 日本株',
        language='en',
        sort_by='publishedAt',
        page_size=10
    )
    for article in articles['articles']:
        rows.append([now, 'ニュース', article['title'], article['description'] or '', article['url'], ''])
except Exception as e:
    print(f'NewsAPI失敗: {e}')

# 適時開示取得
try:
    today = datetime.now(JST).strftime('%Y%m%d')
    url = f'https://www.release.tdnet.info/inbs/I_list_001_{today}.html'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    res = requests.get(url, timeout=10, headers=headers)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    for td_time in soup.select('td.kjTime')[:50]:
        tr = td_time.parent
        time_str = td_time.get_text(strip=True)
        code_td = tr.select_one('td.kjCode')
        name_td = tr.select_one('td.kjName')
        title_td = tr.select_one('td.kjTitle')
        code = code_td.get_text(strip=True) if code_td else ''
        company = name_td.get_text(strip=True) if name_td else ''
        title = title_td.get_text(strip=True) if title_td else ''
        link_tag = title_td.find('a') if title_td else None
        link = 'https://www.release.tdnet.info/inbs/' + link_tag['href'] if link_tag else ''
        rows.append([now, '適時開示', f'{company}（{code}）：{title}', time_str, link, ''])
except Exception as e:
    print(f'適時開示取得失敗: {e}')

# 出来高急増銘柄の取得
WATCH_TICKERS = {
    '7203.T': 'トヨタ自動車', '6758.T': 'ソニーグループ', '9984.T': 'ソフトバンクG',
    '6861.T': 'キーエンス', '8306.T': '三菱UFJ', '9432.T': 'NTT',
    '6902.T': 'デンソー', '7267.T': '本田技研', '6954.T': 'ファナック',
    '8035.T': '東京エレクトロン', '6367.T': 'ダイキン', '4519.T': '中外製薬',
    '9433.T': 'KDDI', '6501.T': '日立製作所', '8058.T': '三菱商事',
    '9983.T': 'ファーストリテイリング', '8316.T': '三井住友FG', '4063.T': '信越化学',
    '6594.T': '日本電産(ニデック)', '7974.T': '任天堂', '6098.T': 'リクルートHD',
    '4661.T': 'オリエンタルランド', '2914.T': '日本たばこ産業', '7741.T': 'HOYA',
    '3382.T': 'セブン&アイHD',
}

try:
    tickers_list = list(WATCH_TICKERS.keys())
    raw = yf.download(tickers_list, period='20d', auto_adjust=True, progress=False)
    vol_df = raw['Volume']

    surge_stocks = []
    for ticker in tickers_list:
        if ticker not in vol_df.columns:
            continue
        vol = vol_df[ticker].dropna()
        if len(vol) < 5:
            continue
        avg_vol = vol.iloc[:-1].mean()
        latest_vol = vol.iloc[-1]
        if avg_vol > 0 and latest_vol >= avg_vol * 2:
            ratio = latest_vol / avg_vol
            surge_stocks.append((ticker, WATCH_TICKERS[ticker], ratio))

    surge_stocks.sort(key=lambda x: x[2], reverse=True)
    for ticker, name, ratio in surge_stocks[:5]:
        rows.append([now, '出来高急増', f'{name}（{ticker}）', f'{ratio:.1f}倍', '', ''])
        print(f'出来高急増: {name} {ratio:.1f}倍')

except Exception as e:
    print(f'出来高急増取得失敗: {e}')

# Sheetsに追記
sheet.append_rows(rows)
print(f'{len(rows)}件追加しました')
