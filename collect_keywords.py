#!/usr/bin/env python3
"""
RSS フィードからキーワードスコアリングでニュースを収集・ランキングするツール。

CLIとして使う場合:
  python collect_keywords.py                        # 収集・Sheets保存・上位10件表示
  python collect_keywords.py --show 20              # 上位20件表示
  python collect_keywords.py --list                 # キーワード一覧（組み込み＋ユーザー定義）
  python collect_keywords.py --add  GROUP KEYWORD   # キーワード追加（keywords.json に保存）
  python collect_keywords.py --remove GROUP KEYWORD # キーワード削除（keywords.json から）

モジュールとして使う場合:
  from collect_keywords import get_unsent, mark_as_sent
  articles = get_unsent(min_score=10)   # 未送信記事を取得
  mark_as_sent([a["id"] for a in articles])
"""
import argparse
import feedparser
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
KEYWORDS_FILE = Path(__file__).parent / 'keywords.json'
DEFAULT_SCORE = 3   # --add で追加したキーワードのデフォルトスコア
SHEET_TYPE    = 'キーワードニュース'

# 組み込みキーワード（キーワード: スコア）
BUILTIN_KEYWORDS: dict[str, int] = {
    # 決算・業績 (高スコア)
    '最高益': 5, '過去最高': 5, 'TOB': 5, 'MBO': 5,
    '上方修正': 4, '下方修正': 4, '自社株買い': 4, '増配': 4, '特別配当': 4,
    '臨時取締役会': 4, '格上げ': 4, '格下げ': 4, 'M&A': 4, '買収': 4, '合併': 4,
    # 業績指標
    '増収': 3, '増益': 3, '黒字転換': 3, '好決算': 3, '目標株価': 3,
    '営業利益': 2, '純利益': 2, '売上高': 2, '経常利益': 2,
    'EPS': 2, 'earnings': 2, 'beat': 2, 'raised guidance': 3,
    'record profit': 3, 'dividend increase': 3, 'analyst upgrade': 3,
    # 機関投資家
    'institutional': 3, 'hedge fund': 3, 'fund manager': 3,
    # 日本株・市場
    '日経': 2, 'TOPIX': 2, 'JPX': 2, '東証': 2, 'プライム': 2,
    'Nikkei': 2, 'Tokyo Stock Exchange': 2, 'Japan stocks': 2,
    # テーマ
    '半導体': 3, '生成AI': 3, 'AI': 2, 'EV': 2, '電気自動車': 2,
    # 主要銘柄
    'トヨタ': 2, 'ソニー': 2, 'ソフトバンク': 2, '任天堂': 2, 'キーエンス': 2,
    '東京エレクトロン': 3, '三菱UFJ': 2, '信越化学': 2, 'デンソー': 2,
    # その他
    '株式分割': 3, '株主総会': 2, '適時開示': 2,
}

# RSSフィード一覧（source名, URL）
RSS_FEEDS: list[tuple[str, str]] = [
    ('Reuters JP',         'https://feeds.reuters.com/reuters/JPTopNews'),
    ('Reuters Business',   'https://feeds.reuters.com/reuters/businessNews'),
    ('Bloomberg Markets',  'https://feeds.bloomberg.com/markets/news.rss'),
    ('Yahoo Finance',      'https://finance.yahoo.com/news/rssindex'),
    ('Investing.com JP',   'https://jp.investing.com/rss/news_301.rss'),
    ('MarketWatch',        'https://feeds.content.dowjones.io/public/rss/mw_topstories'),
]


# ── keywords.json 操作 ─────────────────────────────────────────────────────────

def load_user_keywords() -> dict[str, dict[str, int]]:
    """keywords.json を読み込む。形式: {"グループ名": {"キーワード": スコア, ...}, ...}"""
    if not KEYWORDS_FILE.exists():
        return {}
    try:
        return json.loads(KEYWORDS_FILE.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        print(f'[WARN] keywords.json の読み込みエラー: {e}', file=sys.stderr)
        return {}


def save_user_keywords(data: dict[str, dict[str, int]]) -> None:
    KEYWORDS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def merged_keywords() -> dict[str, int]:
    """組み込み＋ユーザー定義を結合。ユーザー定義が同名の場合は上書き。"""
    result = dict(BUILTIN_KEYWORDS)
    for group_kws in load_user_keywords().values():
        result.update(group_kws)
    return result


# ── スコアリング ──────────────────────────────────────────────────────────────

def _is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def score_text(text: str, keywords: dict[str, int]) -> tuple[int, list[str]]:
    """テキストのキーワードスコアと一致キーワードリストを返す。"""
    if not text:
        return 0, []
    hits: list[str] = []
    total = 0
    lower = text.lower()
    for kw, pts in keywords.items():
        kw_lower = kw.lower()
        if _is_ascii(kw):
            # 英数字キーワードは単語境界でマッチ（部分文字列誤検知防止）
            if re.search(r'\b' + re.escape(kw_lower) + r'\b', lower):
                total += pts
                hits.append(kw)
        else:
            # 日本語キーワードは部分文字列マッチ
            if kw_lower in lower:
                total += pts
                hits.append(kw)
    return total, hits


# ── RSS 収集 ─────────────────────────────────────────────────────────────────

def _fetch_all() -> list[dict]:
    """全フィードを取得し、スコア > 0 の記事を降順で返す。"""
    keywords = merged_keywords()
    articles: list[dict] = []
    now = datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    seen_titles: set[str] = set()

    for source_name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title: str = entry.get('title', '').strip()
                summary: str = entry.get('summary', '').strip()
                link: str = entry.get('link', '')

                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                title_score, title_hits = score_text(title, keywords)
                summary_score, summary_hits = score_text(summary, keywords)
                score = title_score + summary_score
                hits = list(dict.fromkeys(title_hits + summary_hits))

                if score > 0:
                    display_summary = (summary[:120] + '…') if len(summary) > 120 else summary
                    articles.append({
                        'source': source_name,
                        'title': title,
                        'summary': display_summary,
                        'url': link,
                        'score': score,
                        'hits': hits,
                        'collected_at': now,
                    })
        except Exception as e:
            print(f'[WARN] {source_name} 取得失敗: {e}')

    articles.sort(key=lambda x: x['score'], reverse=True)
    return articles


# ── Google Sheets 連携 ────────────────────────────────────────────────────────

def _get_sheet():
    """Google Sheets への接続を返す。環境変数未設定時は None。"""
    sheet_id = os.environ.get('SHEET_ID', '')
    credentials_file = os.environ.get('CREDENTIALS_FILE', 'credentials.json')
    if not sheet_id or not os.path.exists(credentials_file):
        return None
    import gspread
    from google.oauth2.service_account import Credentials as GCreds
    scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = GCreds.from_service_account_file(credentials_file, scopes=scopes)
    return gspread.authorize(creds).open_by_key(sheet_id).sheet1


def _save_to_sheet(articles: list[dict]) -> int:
    """記事リストをシートに保存。保存件数を返す。"""
    sheet = _get_sheet()
    if sheet is None:
        return 0
    rows = [
        [a['collected_at'], SHEET_TYPE, a['title'], str(a['score']), a['url'], '']
        for a in articles
    ]
    if rows:
        sheet.append_rows(rows)
    return len(rows)


# ── 公開 API ─────────────────────────────────────────────────────────────────

def get_unsent(min_score: int = 0) -> list[dict]:
    """スコア >= min_score の未送信キーワードニュースを Sheets から取得する。

    Returns:
        list of dict: id, title, score, url, collected_at
        id は mark_as_sent に渡す Sheets 行番号（1始まり）。
    """
    sheet = _get_sheet()
    if sheet is None:
        raise RuntimeError('SHEET_ID または credentials.json が設定されていません')

    result = []
    for i, row in enumerate(sheet.get_all_values()[1:], start=2):
        if len(row) < 6:
            continue
        ts, type_, title, value, url, sent = row[0], row[1], row[2], row[3], row[4], row[5]
        if type_ != SHEET_TYPE or str(sent).strip() != '':
            continue
        try:
            score = int(value)
        except (ValueError, TypeError):
            continue
        if score >= min_score:
            result.append({
                'id': i,
                'title': title,
                'score': score,
                'url': url,
                'collected_at': ts,
            })
    return result


def mark_as_sent(ids: list[int]) -> None:
    """指定行 ID（Sheets 行番号）を送信済み（1）にマークする。"""
    if not ids:
        return
    sheet = _get_sheet()
    if sheet is None:
        raise RuntimeError('SHEET_ID または credentials.json が設定されていません')
    updates = [{'range': f'F{row_num}', 'values': [[1]]} for row_num in ids]
    sheet.batch_update(updates)


# ── コマンド ──────────────────────────────────────────────────────────────────

def cmd_list() -> None:
    user_data = load_user_keywords()

    print('=== 組み込みキーワード ===')
    by_score: dict[int, list[str]] = {}
    for kw, pts in BUILTIN_KEYWORDS.items():
        by_score.setdefault(pts, []).append(kw)
    for pts in sorted(by_score.keys(), reverse=True):
        kws = '  '.join(sorted(by_score[pts]))
        print(f'  [{pts}pt] {kws}')
    print(f'  合計 {len(BUILTIN_KEYWORDS)} キーワード')

    if user_data:
        print('\n=== ユーザー定義キーワード (keywords.json) ===')
        for group, kws in user_data.items():
            print(f'  [{group}]')
            for kw, pts in kws.items():
                print(f'    [{pts}pt] {kw}')
        total_user = sum(len(v) for v in user_data.values())
        print(f'  合計 {total_user} キーワード / {len(user_data)} グループ')
    else:
        print('\nユーザー定義キーワードなし（--add で追加できます）')

    print(f'\nフィード数: {len(RSS_FEEDS)}')


def cmd_add(group: str, keyword: str) -> None:
    data = load_user_keywords()
    if group not in data:
        data[group] = {}
    if keyword in data[group]:
        print(f'既に登録済み: [{group}] "{keyword}" ({data[group][keyword]}pt)')
        return
    data[group][keyword] = DEFAULT_SCORE
    save_user_keywords(data)
    print(f'追加しました: [{group}] "{keyword}" ({DEFAULT_SCORE}pt) → {KEYWORDS_FILE.name}')


def cmd_remove(group: str, keyword: str) -> None:
    data = load_user_keywords()
    if group not in data or keyword not in data[group]:
        if keyword in BUILTIN_KEYWORDS:
            print(f'"{keyword}" は組み込みキーワードです。削除するには keywords.json に '
                  f'"{keyword}": 0 と書いてスコアを0にしてください。')
        else:
            print(f'見つかりません: [{group}] "{keyword}"')
        return
    del data[group][keyword]
    if not data[group]:
        del data[group]
    save_user_keywords(data)
    print(f'削除しました: [{group}] "{keyword}"')


def cmd_collect(show_n: int) -> None:
    user_count = sum(len(v) for v in load_user_keywords().values())
    total = len(BUILTIN_KEYWORDS) + user_count
    print(f'RSSフィード取得中 … キーワード{total}件 / フィード{len(RSS_FEEDS)}本 / 上位{show_n}件表示')

    all_articles = _fetch_all()

    saved = _save_to_sheet(all_articles)
    if saved:
        print(f'{saved}件をシートに保存しました')

    results = all_articles[:show_n]
    if not results:
        print('該当記事なし（スコア0以上の記事が見つかりませんでした）')
        return

    print(f'\n=== キーワードスコア上位 {len(results)} 件 ===')
    for i, a in enumerate(results, 1):
        hits_str = ', '.join(a['hits'][:5])
        print(f'\n[{i:2d}] スコア:{a["score"]:3d}  {a["source"]}  ({hits_str})')
        print(f'      {a["title"]}')
        if a['summary']:
            print(f'      {a["summary"]}')
        if a['url']:
            print(f'      {a["url"]}')

    print(f'\n収集日時: {all_articles[0]["collected_at"] if all_articles else "-"}')


def main() -> None:
    parser = argparse.ArgumentParser(description='RSSキーワードスコアリングによるニュース収集')
    parser.add_argument('--list',   action='store_true',
                        help='キーワード一覧を表示して終了')
    parser.add_argument('--show',   type=int, default=10, metavar='N',
                        help='上位N件を表示（デフォルト: 10）')
    parser.add_argument('--add',    nargs=2, metavar=('GROUP', 'KEYWORD'),
                        help='キーワードを追加: --add グループ名 "キーワード"')
    parser.add_argument('--remove', nargs=2, metavar=('GROUP', 'KEYWORD'),
                        help='キーワードを削除: --remove グループ名 "キーワード"')
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.add:
        cmd_add(args.add[0], args.add[1])
    elif args.remove:
        cmd_remove(args.remove[0], args.remove[1])
    else:
        cmd_collect(args.show)


if __name__ == '__main__':
    main()
