import gspread
from google.oauth2.service_account import Credentials
import yfinance as yf
from newsapi import NewsApiClient
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from config import *

# Google Sheets接続
scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

rows = []
now = datetime.now().strftime('%Y-%m-%d %H:%M')

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
    today = datetime.now().strftime('%Y%m%d')
    url = f'https://www.release.tdnet.info/inbs/I_list_001_{today}.html'
    res = requests.get(url, timeout=10)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    for row in soup.select('tr.main-list')[:50]:
        cols = row.select('td')
        if len(cols) >= 4:
            time_str = cols[0].get_text(strip=True)
            company = cols[2].get_text(strip=True)
            title = cols[3].get_text(strip=True)
            link_tag = cols[3].find('a')
            link = 'https://www.release.tdnet.info' + link_tag['href'] if link_tag else ''
            rows.append([now, '適時開示', f'{company}：{title}', time_str, link, ''])
except Exception as e:
    print(f'適時開示取得失敗: {e}')

# Sheetsに追記
sheet.append_rows(rows)
print(f'{len(rows)}件追加しました')
