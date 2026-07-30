import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta
import os

CREDENTIALS_FILE = os.environ.get('CREDENTIALS_FILE', 'credentials.json')
SHEET_ID = os.environ.get('SHEET_ID', '')
RETENTION_DAYS = 15

scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

all_rows = sheet.get_all_values()

JST = timezone(timedelta(hours=9))
cutoff = datetime.now(JST) - timedelta(days=RETENTION_DAYS)

old_rows = []
for i, row in enumerate(all_rows[1:], start=2):
    if not row or not row[0]:
        continue
    try:
        ts = datetime.strptime(row[0], '%Y-%m-%d %H:%M').replace(tzinfo=JST)
        if ts < cutoff:
            old_rows.append(i)
    except ValueError:
        pass

for row_num in sorted(old_rows, reverse=True):
    sheet.delete_rows(row_num)
print(f'{len(old_rows)}行削除（{RETENTION_DAYS}日超過）')

all_rows = sheet.get_all_values()
total = max(0, len(all_rows) - 1)
print(f'残り{total}件')
