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

# 機関投資家が注目するニュース取得（業界問わず）
try:
    newsapi = NewsApiClient(api_key=NEWSAPI_KEY)
    articles = newsapi.get_everything(
        q=(
            'institutional investor OR hedge fund OR fund manager OR '
            'earnings beat OR raised guidance OR analyst upgrade OR '
            'Japan stocks OR Tokyo Stock Exchange OR JPX OR Nikkei'
        ),
        language='en',
        sort_by='publishedAt',
        page_size=15
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

# 出来高急増銘柄の取得（日経225主要50銘柄）
WATCH_TICKERS = {
    # 輸送機器
    '7203.T': 'トヨタ自動車', '7267.T': '本田技研', '7201.T': '日産自動車', '7270.T': 'SUBARU',
    # 電機・精密
    '6758.T': 'ソニーグループ', '6861.T': 'キーエンス', '6954.T': 'ファナック',
    '6902.T': 'デンソー', '6501.T': '日立製作所', '6702.T': '富士通',
    '6723.T': 'ルネサスエレクトロニクス', '6762.T': 'TDK', '6971.T': '京セラ',
    '7751.T': 'キヤノン', '7733.T': 'オリンパス',
    # 半導体・電子部品
    '8035.T': '東京エレクトロン', '4063.T': '信越化学', '6594.T': '日本電産(ニデック)',
    # 通信・IT
    '9984.T': 'ソフトバンクG', '9432.T': 'NTT', '9433.T': 'KDDI',
    '6098.T': 'リクルートHD', '3659.T': 'ネクソン',
    # 金融
    '8306.T': '三菱UFJ', '8316.T': '三井住友FG', '8411.T': 'みずほFG',
    # 商社
    '8058.T': '三菱商事', '8031.T': '三井物産', '8053.T': '住友商事',
    # 小売・消費
    '9983.T': 'ファーストリテイリング', '3382.T': 'セブン&アイHD',
    # 製薬・医療
    '4519.T': '中外製薬', '4568.T': '第一三共', '4502.T': '武田薬品', '4543.T': 'テルモ',
    # 素材・化学
    '3407.T': '旭化成',
    # 機械
    '6301.T': '小松製作所', '6273.T': 'SMC',
    # エンタメ・サービス
    '7974.T': '任天堂', '4661.T': 'オリエンタルランド', '9602.T': '東宝',
    # その他製造
    '6367.T': 'ダイキン', '5108.T': 'ブリヂストン',
    '7741.T': 'HOYA', '2914.T': '日本たばこ産業',
    # 食品・生活
    '2802.T': '味の素', '2503.T': 'キリンHD',
    # 富士フイルム・資生堂
    '4901.T': '富士フイルム', '4911.T': '資生堂',
}

try:
    tickers_list = list(WATCH_TICKERS.keys())
    raw = yf.download(tickers_list, period='20d', auto_adjust=True, progress=False)
    vol_df = raw['Volume']

    # 実際の最新取引日を取得（yfinanceが返したデータの最終日）
    latest_trading_date = vol_df.index[-1].strftime('%Y/%m/%d')

    surge_stocks = []
    for ticker in tickers_list:
        if ticker not in vol_df.columns:
            continue
        vol = vol_df[ticker].dropna()
        if len(vol) < 5:
            continue
        avg_vol = vol.iloc[:-1].mean()
        target_vol = vol.iloc[-1]
        if avg_vol > 0 and target_vol >= avg_vol * 2:
            ratio = target_vol / avg_vol
            surge_stocks.append((ticker, WATCH_TICKERS[ticker], ratio))

    surge_stocks.sort(key=lambda x: x[2], reverse=True)
    for ticker, name, ratio in surge_stocks[:10]:
        rows.append([now, '出来高急増', f'{name}（{ticker}）', f'{latest_trading_date} {ratio:.1f}倍', '', ''])
        print(f'出来高急増: {name} {latest_trading_date} {ratio:.1f}倍')

except Exception as e:
    print(f'出来高急増取得失敗: {e}')

# Sheetsに追記
sheet.append_rows(rows)
print(f'{len(rows)}件追加しました')
