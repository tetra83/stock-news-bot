import gspread
from google.oauth2.service_account import Credentials
import anthropic
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import os
from collect_keywords import get_unsent, mark_as_sent
from collect_market import format_market_summary

CREDENTIALS_FILE = os.environ.get('CREDENTIALS_FILE', 'credentials.json')
SHEET_ID = os.environ.get('SHEET_ID', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = 'claude-sonnet-4-6'
GMAIL_ADDRESS = os.environ.get('GMAIL_ADDRESS', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

# フィルタキーワード（適時開示・ニュースの重要度判定）
FILTER_KEYWORDS = [
    '増収', '増益', '上方修正', '業績修正', '増配', '特別配当',
    '過去最高', '最高益', '好決算', '黒字転換', 'TOB', 'M&A', '買収', '合併',
    '自社株買い', '株式分割',
    'beat', 'raised guidance', 'dividend increase', 'record profit', 'upgraded',
    'institutional', 'hedge fund', 'analyst upgrade', 'earnings',
]

scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

records = sheet.get_all_records()
unsent = [r for r in records if str(r.get('sent', '')).strip() == '']

if not unsent:
    print('未送信データなし')
    exit()

# フィルタ：出来高急増は全件、適時開示・ニュースはキーワード一致のみ
def is_important(r):
    if r.get('type') == '出来高急増':
        return True
    text = f"{r.get('name','')} {r.get('value','')}".lower()
    return any(kw.lower() in text for kw in FILTER_KEYWORDS)

filtered = [r for r in unsent if is_important(r)]

# キーワードニュース（スコア10以上）取得
try:
    keyword_articles = get_unsent(min_score=10)
except Exception as e:
    print(f'[WARN] キーワードニュース取得失敗: {e}')
    keyword_articles = []

if not filtered and not keyword_articles:
    print('重要データなし、送信スキップ')
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if str(row[5]).strip() == '':
            sheet.update_cell(i, 6, 1)
    exit()

text = '\n'.join([f"{r['type']} | {r['name']} | {r['value']}" for r in filtered])

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

surge_stocks     = [r for r in filtered if r.get('type') == '出来高急増']
news_items       = [r for r in filtered if r.get('type') == 'ニュース']
disclosure_items = [r for r in filtered if r.get('type') == '適時開示']

surge_section = ''
if surge_stocks:
    surge_list = '\n'.join([f"- {r['name']} ({r['value']})" for r in surge_stocks])
    surge_section = f"""
【出来高急増銘柄（取引日付き）】
{surge_list}
↑ 各銘柄について、その日に出来高が急増した背景・理由・機関投資家の注目ポイントを3行で説明してください。"""

news_section = ''
if news_items:
    news_list = '\n'.join([f"- {r['name']}" for r in news_items])
    news_section = f"""
【機関投資家向けニュース】
{news_list}"""

disclosure_section = ''
if disclosure_items:
    disc_list = '\n'.join([f"- {r['name']}" for r in disclosure_items])
    disclosure_section = f"""
【重要適時開示】
{disc_list}"""

keyword_section = ''
if keyword_articles:
    kw_list = '\n'.join([f"- {a['title']} (スコア:{a['score']})" for a in keyword_articles])
    keyword_section = f"""
【キーワードスコア上位ニュース（RSS）】
{kw_list}"""

response = claude.messages.create(
    model=ANTHROPIC_MODEL,
    max_tokens=2000,
    messages=[{
        'role': 'user',
        'content': f"""以下は本日収集した株式市場データです。市況概況は不要です。
以下の順で日本語で簡潔にまとめてください：

1. 出来高急増銘柄：各銘柄の急増背景・関連ニュース・機関投資家の注目ポイントを3行で
2. 機関投資家・ヘッジファンドが注目しているニュースのポイント（業界問わず）
3. 重要な適時開示（増収増益・上方修正・M&A・自社株買いなど）
4. キーワードスコア上位ニュースのポイント（スコアが高いほど重要度が高い）
{surge_section}{news_section}{disclosure_section}{keyword_section}

生データ：
{text}"""
    }]
)

summary = response.content[0].text

# 市場概況を末尾に付記（Claude 要約の対象外・数値そのまま）
market_summary = format_market_summary()
body = summary
if market_summary:
    body += f'\n\n{market_summary}'

now = datetime.now().strftime('%Y-%m-%d %H:%M')
msg = MIMEText(body, 'plain', 'utf-8')
msg['Subject'] = f'株式情報ダイジェスト {now}'
msg['From'] = GMAIL_ADDRESS
msg['To'] = GMAIL_ADDRESS

with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
    smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    smtp.send_message(msg)

print(f'メール送信完了：{len(filtered)}件 + キーワード{len(keyword_articles)}件処理')

# 送信済みフラグ更新
if keyword_articles:
    try:
        mark_as_sent([a['id'] for a in keyword_articles])
    except Exception as e:
        print(f'[WARN] キーワードニュース送信済みマーク失敗: {e}')

all_rows = sheet.get_all_values()
for i, row in enumerate(all_rows[1:], start=2):
    if str(row[5]).strip() == '':
        sheet.update_cell(i, 6, 1)
