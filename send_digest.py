import gspread
from google.oauth2.service_account import Credentials
import anthropic
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import os
CREDENTIALS_FILE = os.environ.get('CREDENTIALS_FILE', 'credentials.json')
SHEET_ID = os.environ.get('SHEET_ID', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = 'claude-sonnet-4-5'
GMAIL_ADDRESS = os.environ.get('GMAIL_ADDRESS', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

# フィルタキーワード
FILTER_KEYWORDS = [
    '増収', '増益', '上方修正', '業績修正', '増配', '特別配当',
    '過去最高', '最高益', '好決算', '黒字転換',
    'beat', 'raised guidance', 'dividend increase', 'record profit', 'upgraded'
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

# フィルタ：株価・出来高急増は全件、適時開示・ニュースはキーワード一致のみ
def is_important(r):
    if r.get('type') in ('株価', '出来高急増'):
        return True
    text = f"{r.get('name','')} {r.get('value','')}".lower()
    return any(kw.lower() in text for kw in FILTER_KEYWORDS)

filtered = [r for r in unsent if is_important(r)]

if not filtered:
    print('重要データなし、送信スキップ')
    # 未重要データもsentフラグを立てる
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if str(row[5]).strip() == '':
            sheet.update_cell(i, 6, 1)
    exit()

text = '\n'.join([f"{r['type']} | {r['name']} | {r['value']}" for r in filtered])

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
surge_stocks = [r for r in filtered if r.get('type') == '出来高急増']
surge_section = ''
if surge_stocks:
    surge_list = '\n'.join([f"- {r['name']} ({r['value']})" for r in surge_stocks])
    surge_section = f"\n\n【出来高急増銘柄】\n{surge_list}\n↑ 各銘柄について出来高急増の理由を3行で説明してください（背景・関連ニュース・注目点）。"

response = claude.messages.create(
    model=ANTHROPIC_MODEL,
    max_tokens=1500,
    messages=[{
        'role': 'user',
        'content': f"""以下は株価・経済ニュース・適時開示・出来高急増銘柄のデータです。
好決算・業績上方修正・増配に関する情報を優先して、日本株への影響を日本語で簡潔にまとめてください。{surge_section}

{text}"""
    }]
)
summary = response.content[0].text
now = datetime.now().strftime('%Y-%m-%d %H:%M')
msg = MIMEText(summary, 'plain', 'utf-8')
msg['Subject'] = f'市場ダイジェスト {now}'
msg['From'] = GMAIL_ADDRESS
msg['To'] = GMAIL_ADDRESS

with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
    smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    smtp.send_message(msg)

print(f'メール送信完了：{len(filtered)}件処理')

# 送信済みフラグ更新
all_rows = sheet.get_all_values()
for i, row in enumerate(all_rows[1:], start=2):
    if str(row[5]).strip() == '':
        sheet.update_cell(i, 6, 1)
