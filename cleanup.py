import gspread
from google.oauth2.service_account import Credentials
import os
CREDENTIALS_FILE = os.environ.get('CREDENTIALS_FILE', 'credentials.json')
SHEET_ID = os.environ.get('SHEET_ID', '')

MAX_UNSENT = 1000

scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

all_rows = sheet.get_all_values()

# sent=1の行を削除（翌日分を新鮮な状態で蓄積するため）
sent_rows = [i+2 for i, row in enumerate(all_rows[1:]) if str(row[5]).strip() == '1']
for row_num in sorted(sent_rows, reverse=True):
    sheet.delete_rows(row_num)
print(f'送信済み{len(sent_rows)}行削除')

# 再取得して未送信が1000件超なら古い順に削除
all_rows = sheet.get_all_values()
unsent_rows = [i+2 for i, row in enumerate(all_rows[1:]) if str(row[5]).strip() == '']
if len(unsent_rows) > MAX_UNSENT:
    to_delete = unsent_rows[:len(unsent_rows) - MAX_UNSENT]
    for row_num in sorted(to_delete, reverse=True):
        sheet.delete_rows(row_num)
    print(f'上限超過{len(to_delete)}行削除')
else:
    print(f'未送信{len(unsent_rows)}件、上限以内')
