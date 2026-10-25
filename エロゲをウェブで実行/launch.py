# -*- coding: utf-8 -*-
"""C:\\Moonlight-web-stream に置いて、このファイルだけ実行する。

python launch.py

同じ窓で web-server.exe を起こし、ボタンページを出す。
runner.py は Sunshine のコマンドのまま変えない。
カレントに増やすのは buttons.json / selected.txt / session.json。

【変更点】
- 8765を 0.0.0.0 で待ち受け → 他PC・スマホからアクセス可能
- /select 時にアクセスしてきたHostを使って stream URL を生成
- 起動前に既存の web-server.exe を自動終了
- 収集開始前に対象フォルダ数を表示
- 第1段階で全フォルダに対し 日本語Wikipedia API で正確タイトルを推定・表示
- 第2段階でその推定タイトルを使って批評空間を検索
- 批評空間が落ちているときは同ディレクトリの pov_list_with_cg.html を検索・カバー取得に使用
- FORCE_RECOLLECT=True は起動時1回だけ全再収集（完了後はフォルダ変化時のみ差分更新）
- フォルダ名セットが変わらない限り再検索しない（無限ループ防止）
- 既存の cover 画像（png/jpg/jpeg/webp/gif/bmp/avif）があれば再取得せず使う
- ローカル/外部アクセスで stream ポートを自動切り替え
"""
# ======================== 設定 ========================
TEST_LIMIT = 0
USE_EXISTING_BUTTONS = True
# True のとき「起動時の1回だけ」既存 buttons.json を無視して全再収集。完了後は自動で False 扱いになる
FORCE_RECOLLECT = False
PORT = 8765
BIND_ADDRESS = "0.0.0.0:8090"
HOST_ID = "1180768552"
APP_ID = "1093255277"
SUNSHINE_USER = "hasegawa"
SUNSHINE_PASS = "9080hake"
# 外部公開ポート（PR-400MIの静的NAPTで8090に割り当てた番号）
EXTERNAL_STREAM_PORT = 4296
# =====================================================

import html
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
CF_STREAM_URL_FILE = HERE.parent / "cf_stream_url.json"  # StartServer.py が書き出す

def get_cf_stream_url() -> str:
    """Cloudflare Stream URL を取得（あれば）"""
    try:
        if CF_STREAM_URL_FILE.is_file():
            data = json.loads(CF_STREAM_URL_FILE.read_text(encoding="utf-8"))
            return (data.get("url") or "").rstrip("/")
    except Exception:
        pass
    return ""
PATH_FILE = HERE / "eroge_path.json"
BUTTONS = HERE / "buttons.json"
EXCLUDED = HERE / "excluded_folders.json"
SELECTED = HERE / "selected.txt"
STATE = HERE / "session.json"
WEB = HERE / "web-server.exe"
# 批評空間が落ちているときのローカルキャッシュ（同ディレクトリに置く）
LOCAL_POV_HTML = HERE / "pov_list_with_cg.html"
BASE = "https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/"
SUNSHINE = "https://localhost:47990"
SKIP = (
    "uninstall", "setup", "support", "config", "setting", "crash", "report",
    "redist", "vcredist", "install", "update", "patch", "dxsetup", "unity",
    "アンインストール", "サポート", "設定", "導入", "エンジン",
)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "ja"}
web_proc = None

# ローカルHTMLキャッシュ（起動時に1回読み込み）
_local_games: list[dict] | None = None
_egs_available: bool | None = None


def http():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def abs_url(src):
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("http"):
        return src
    return urljoin(BASE, src)


def exe_candidates(folder: Path):
    return [
        exe for exe in folder.rglob("*.exe")
        if not any(word in exe.stem.casefold() for word in SKIP)
    ]


def pick_exe(folder: Path, title: str):
    exes = exe_candidates(folder)
    if not exes:
        return ""

    def score(exe: Path):
        n = exe.stem.casefold()
        points = max(
            SequenceMatcher(None, n, title.casefold()).ratio(),
            SequenceMatcher(None, n, folder.name.casefold()).ratio(),
        )
        if "64" in n:
            points += 0.2
        if "32" in n or "86" in n:
            points -= 0.1
        return points

    return str(max(exes, key=score))


def _clean_wiki_query(folder_name: str) -> str:
    """フォルダ名から検索用クエリを作る（ブランド・版表記などを除去）"""
    s = folder_name.strip()
    s = re.sub(r"^[\[][^\]]+[\]]\s*", "", s)
    s = re.sub(r"^【[^】]+】\s*", "", s)
    s = re.sub(
        r"[（(][^）)]*(ダウンロード|リマスター|Complete|Edition|HD|通常版|初回|"
        r"パッケージ|DL版|全年齢|18禁)[^）)]*[）)]",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = s.replace("_", " ").replace("＊", "*")
    s = re.sub(r"\s+", " ", s).strip()
    return s or folder_name.strip()


def _norm_cmp(text: str) -> str:
    t = text.casefold()
    t = t.replace("_", "").replace(" ", "").replace("　", "")
    t = t.replace("〜", "~").replace("～", "~").replace("－", "-").replace("—", "-")
    return t


def google_find_accurate_title(folder_name: str, silent: bool = False) -> str:
    """日本語Wikipediaの opensearch（サイト内検索相当）で正確タイトルを推定。

    原則: APIの順位（1位）を尊重する。
    SequenceMatcher だけで並べ替えると「11eyes」→「11-elevens-」のような誤マッチが起きるため使わない。
    """
    def log(msg):
        if not silent:
            print(msg, flush=True)
            sys.stdout.flush()

    query = _clean_wiki_query(folder_name)
    log(f"  [DEBUG] Wikipedia検索開始: {query}")

    headers = {
        "User-Agent": "ErogeLaunch/1.2 (local batch; personal use)",
        "Accept": "application/json",
        "Accept-Language": "ja",
    }
    api_url = "https://ja.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": query,
        "limit": 8,
        "namespace": 0,
        "format": "json",
    }

    titles = []
    urls = []
    last_err = None
    for attempt in range(6):
        try:
            r = requests.get(api_url, params=params, headers=headers, timeout=20)
            if r.status_code == 429:
                wait = min(2 ** attempt, 60)
                log(f"  [WARN] 429 Too Many Requests → {wait}s 待機")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            titles = data[1] if len(data) > 1 else []
            urls = data[3] if len(data) > 3 else []
            break
        except Exception as e:
            last_err = e
            time.sleep(min(2 ** attempt, 20))
    else:
        log(f"  Wikipedia検索失敗: {type(last_err).__name__}: {last_err}")
        return folder_name

    log(f"  [DEBUG] ヒット数: {len(titles)}")
    if not titles:
        log("  Wikipediaにヒットなし → フォルダ名をそのまま使用")
        return folder_name

    qn = _norm_cmp(query)
    game_hints = (
        "eyes", "days", "nine", "d.c", "ダ・カーポ", "シリーズ",
        "〜", "～", "ゲーム", "アダルト", "美少女", "visual", "novel", "エロ",
    )

    chosen = None
    # 1) クエリがタイトルに含まれるもの（API順位を保ったまま）
    for t in titles:
        if qn and qn in _norm_cmp(t):
            chosen = t
            break
    # 2) ゲーム系ヒント語（順位順）
    if chosen is None:
        for t in titles:
            low = t.lower()
            if any(h in low for h in game_hints):
                chosen = t
                break
    # 3) それでも無ければ opensearch の1位
    if chosen is None:
        chosen = titles[0]

    idx = titles.index(chosen) if chosen in titles else 0
    wiki_url = urls[idx] if idx < len(urls) else ""
    log(f"  推定タイトル: {chosen}")
    if wiki_url:
        log(f"  Wiki URL   : {wiki_url}")
    return chosen


def load_local_pov() -> list[dict]:
    """カレントの pov_list_with_cg.html を読み込み、検索用リストを返す（1回だけ）。"""
    global _local_games
    if _local_games is not None:
        return _local_games
    if not LOCAL_POV_HTML.is_file():
        print(f"  [WARN] ローカルキャッシュなし: {LOCAL_POV_HTML}", flush=True)
        _local_games = []
        return _local_games
    print(f"ローカルキャッシュ読込中: {LOCAL_POV_HTML.name} …", flush=True)
    t0 = time.time()
    try:
        text = LOCAL_POV_HTML.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        games = []
        for card in soup.select("article.card"):
            gid = card.get("data-id") or ""
            h2a = card.select_one("h2 a")
            title = h2a.get_text(strip=True) if h2a else ""
            if not title:
                continue
            brand_el = card.select_one(".brand")
            date_el = card.select_one(".date")
            img = card.select_one(".thumb img")
            href = h2a.get("href", "") if h2a else ""
            games.append({
                "id": gid,
                "title": title,
                "brand": brand_el.get_text(strip=True) if brand_el else "",
                "date": date_el.get_text(strip=True) if date_el else "",
                "median": card.get("data-median") or "",
                "url": href or (BASE + f"game.php?game={gid}" if gid else ""),
                "cover_url": img.get("src", "") if img else "",
            })
        _local_games = games
        print(f"  ローカルキャッシュ: {len(games)} 件 ({time.time() - t0:.1f}s)", flush=True)
    except Exception as e:
        print(f"  [WARN] ローカルキャッシュ読込失敗: {e}", flush=True)
        _local_games = []
    return _local_games


def search_local(word: str, limit: int = 20) -> list[dict]:
    """ローカルHTMLからタイトル部分一致・類似度で検索。"""
    games = load_local_pov()
    if not games or not word:
        return []
    q = word.casefold().strip()
    qn = re.sub(r"[\s_　]+", "", q)
    scored = []
    for g in games:
        t = g["title"].casefold()
        tn = re.sub(r"[\s_　]+", "", t)
        if qn and qn in tn:
            score = 0.85 + min(0.15, len(qn) / max(len(tn), 1) * 0.15)
        else:
            score = SequenceMatcher(None, q, t).ratio()
        if score >= 0.35:
            scored.append((score, g))
    scored.sort(key=lambda x: x[0], reverse=True)
    # search() と同じ形の dict を返す（cover_url は後段用に保持）
    rows = []
    for score, g in scored[:limit]:
        rows.append({
            "id": g["id"],
            "title": g["title"],
            "brand": g["brand"],
            "date": g["date"],
            "median": g["median"],
            "url": g["url"],
            "cover_url": g.get("cover_url", ""),
            "_score": score,
            "_source": "local",
        })
    return rows


def check_egs_available(s) -> bool:
    """批評空間が生きているか簡易チェック（結果をキャッシュ）。"""
    global _egs_available
    if _egs_available is not None:
        return _egs_available
    try:
        r = s.get(BASE + "kensaku.php", params={"category": "game", "word": "test", "mode": "normal"}, timeout=8)
        _egs_available = r.status_code == 200 and len(r.content) > 500
    except Exception:
        _egs_available = False
    if _egs_available:
        print("批評空間: オンライン", flush=True)
    else:
        print("批評空間: オフライン → ローカルキャッシュを使用します", flush=True)
    return _egs_available


def search(s, word):
    """批評空間検索。落ちている／失敗時はローカル pov_list_with_cg.html にフォールバック。"""
    if check_egs_available(s):
        try:
            r = s.get(
                BASE + "kensaku.php",
                params={"category": "game", "word_category": "name", "word": word, "mode": "normal"},
                timeout=30,
            )
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            box = soup.find(id="result") or soup
            rows = []
            for tr in box.find_all("tr"):
                a = tr.find("a", href=re.compile(r"game\.php\?game=\d+"))
                if not a:
                    continue
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                m = re.search(r"game=(\d+)", a.get("href", ""))
                rows.append({
                    "id": m.group(1) if m else "",
                    "title": a.get_text(strip=True),
                    "brand": tds[1] if len(tds) > 1 else "",
                    "date": tds[2] if len(tds) > 2 else "",
                    "median": tds[3] if len(tds) > 3 else "",
                    "url": abs_url(a.get("href", "")),
                    "cover_url": "",
                    "_source": "egs",
                })
            if rows:
                return rows
        except Exception as e:
            print(f"  [WARN] 批評空間検索失敗 → ローカルへ: {e}", flush=True)
            global _egs_available
            _egs_available = False

    local_rows = search_local(word)
    if local_rows:
        print(f"  [local] 「{word}」→ {len(local_rows)} 件", flush=True)
    return local_rows


def best(word, rows):
    if not rows:
        return None
    return max(rows, key=lambda row: SequenceMatcher(None, word.casefold(), row["title"].casefold()).ratio())


# カバー画像として認識するファイル名（拡張子なし）と対応拡張子
COVER_BASENAMES = ("cover", "Cover", "COVER", "folder", "Folder")
COVER_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif")
# これ未満は壊れた／空カバーとみなして使わない（黒サムネ対策）
MIN_COVER_BYTES = 4096


def _is_usable_cover(path: Path | str | None) -> bool:
    """実在し、最低サイズ以上の画像ファイルか。"""
    if not path:
        return False
    p = Path(path)
    try:
        return p.is_file() and p.stat().st_size >= MIN_COVER_BYTES
    except OSError:
        return False


def find_existing_cover(*dirs: Path | str | None) -> str:
    """指定ディレクトリ内のカバー候補から使えるもの（大きい順）を返す。png/jpg/webp 等対応。"""
    candidates: list[Path] = []
    seen_dirs: set[Path] = set()
    for d in dirs:
        if not d:
            continue
        p = Path(d)
        if p.is_file():
            p = p.parent
        if not p.is_dir() or p in seen_dirs:
            continue
        seen_dirs.add(p)
        try:
            for cand in p.iterdir():
                if not cand.is_file():
                    continue
                name = cand.name.casefold()
                ext = cand.suffix.casefold()
                if ext not in COVER_EXTS:
                    continue
                stem = cand.stem.casefold()
                if stem in {b.casefold() for b in COVER_BASENAMES} or name.startswith("cover."):
                    if _is_usable_cover(cand):
                        candidates.append(cand)
        except OSError:
            pass
        for base in COVER_BASENAMES:
            for ext in COVER_EXTS:
                cand = p / f"{base}{ext}"
                if cand not in candidates and _is_usable_cover(cand):
                    candidates.append(cand)
    if not candidates:
        return ""
    best = max(candidates, key=lambda c: c.stat().st_size)
    return str(best)


def _ext_from_url_or_type(url: str, content_type: str = "") -> str:
    """URL または Content-Type から保存用拡張子を決める。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "avif" in ct:
        return ".avif"
    if "bmp" in ct:
        return ".bmp"
    path = url.split("?", 1)[0].casefold()
    for ext in COVER_EXTS:
        if path.endswith(ext):
            return ext
    return ".jpg"


def cover_of(
    s,
    game_id,
    dest_dir: Path,
    cover_url: str = "",
    *,
    reuse_existing: bool = True,
) -> str:
    """カバー画像を用意する。

    - reuse_existing=True のとき、使えるサイズの cover.* があれば再DLしない
    - 極小・壊れたファイルは無視して取り直す
    - 新規取得時は URL / Content-Type に合わせた拡張子で保存
    """
    dest_dir = Path(dest_dir)
    if dest_dir.is_file():
        dest_dir = dest_dir.parent

    if reuse_existing:
        existing = find_existing_cover(dest_dir)
        if existing:
            return existing

    url = cover_url or ""
    if not url and game_id and check_egs_available(s):
        try:
            r = s.get(BASE + f"game.php?game={game_id}", timeout=30)
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            img = soup.select_one("#main_image img") or soup.select_one(".tooltip img")
            url = abs_url(img.get("src")) if img and img.get("src") else ""
        except Exception:
            url = ""
    if not url and game_id:
        for g in load_local_pov():
            if str(g.get("id")) == str(game_id) and g.get("cover_url"):
                url = g["cover_url"]
                break
    if not url:
        return ""
    try:
        r = s.get(url, timeout=30)
        data = r.content
        if not data or len(data) < MIN_COVER_BYTES:
            print(f"  [WARN] カバーが小さすぎる/空のため保存しない ({len(data) if data else 0} bytes)", flush=True)
            return ""
        ext = _ext_from_url_or_type(url, r.headers.get("Content-Type", ""))
        dest = dest_dir / f"cover{ext}"
        dest.write_bytes(data)
        return str(dest)
    except Exception as e:
        print(f"  [WARN] カバー取得失敗: {e}", flush=True)
        return ""


def load_saved():
    if BUTTONS.exists():
        return json.loads(BUTTONS.read_text(encoding="utf-8"))
    return []


def save_buttons(buttons):
    BUTTONS.write_text(json.dumps(buttons, ensure_ascii=False, indent=2), encoding="utf-8")


def eroge_root():
    if PATH_FILE.exists():
        try:
            text = json.loads(PATH_FILE.read_text(encoding="utf-8")).get("path", "").strip()
        except json.JSONDecodeError:
            text = ""
        if text and Path(text).exists():
            return Path(text)
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    chosen = filedialog.askdirectory(title="エロゲフォルダ")
    root.destroy()
    if not chosen:
        raise SystemExit("フォルダが選択されていない")
    PATH_FILE.write_text(json.dumps({"path": chosen}, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(chosen)


def load_excluded() -> set[str]:
    """エディタで削除したフォルダ名（再収集しない）。"""
    if not EXCLUDED.is_file():
        return set()
    try:
        data = json.loads(EXCLUDED.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
        if isinstance(data, dict):
            return {str(x) for x in data.get("folders", [])}
    except (json.JSONDecodeError, OSError):
        pass
    return set()


def game_folders():
    root = eroge_root()
    if not root.exists():
        return []
    excluded = load_excluded()
    found = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if folder.name in excluded:
            continue
        if exe_candidates(folder):
            found.append(folder)
    if TEST_LIMIT > 0:
        return found[:TEST_LIMIT]
    return found


# 起動後の「強制再収集は1回だけ」フラグ（FORCE_RECOLLECT を消費したら False になる）
_force_recollect_once = FORCE_RECOLLECT
# 直前に処理したフォルダ名セット（変化がなければ全再検索はしない）
_last_folder_names: frozenset[str] | None = None
# 画面リロード用リビジョン（フォルダ追加・カバー差し替えなどで変わる）
_buttons_rev: int = 0


def _folder_name_set(folders: list[Path]) -> frozenset[str]:
    return frozenset(f.name for f in folders)


def _cover_stamp(path: str | Path | None) -> str:
    if not path:
        return "0"
    try:
        st = Path(path).stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "0"


def _buttons_signature(buttons: list[dict]) -> str:
    parts = []
    for b in buttons:
        cov = b.get("cover") or ""
        parts.append(
            f"{b.get('folder','')}|{b.get('name','')}|{b.get('brand','')}|"
            f"{b.get('exe','')}|{cov}|{_cover_stamp(cov)}"
        )
    return "\n".join(parts)


def _buttons_file_mtime() -> float:
    try:
        return BUTTONS.stat().st_mtime if BUTTONS.is_file() else 0.0
    except OSError:
        return 0.0


def _bump_rev():
    global _buttons_rev
    _buttons_rev += 1
    return _buttons_rev


def refresh_covers_on_disk(buttons: list[dict], folders: list[Path] | None = None) -> tuple[list[dict], bool]:
    """既存ボタンの cover をディスク上の最新・有効なファイルに合わせて更新（再検索はしない）。"""
    folder_map = {f.name: f for f in (folders or [])}
    changed = False
    out = []
    for b in buttons:
        nb = dict(b)
        cov = nb.get("cover") or ""
        folder = folder_map.get(nb.get("folder", ""))
        found = find_existing_cover(
            Path(nb["exe"]).parent if nb.get("exe") else None,
            folder,
        )
        # 登録パスが壊れている／極小 → ディスクの有効なカバーへ差し替え
        if found and found != cov:
            if not _is_usable_cover(cov):
                nb["cover"] = found
                changed = True
            else:
                try:
                    if Path(found).stat().st_size > Path(cov).stat().st_size:
                        nb["cover"] = found
                        changed = True
                    elif Path(found).stat().st_mtime > Path(cov).stat().st_mtime + 1:
                        nb["cover"] = found
                        changed = True
                except OSError:
                    nb["cover"] = found
                    changed = True
        elif not found and cov and not _is_usable_cover(cov):
            nb["cover"] = ""
            changed = True
        out.append(nb)
    return out, changed


def sync_buttons(force: bool | None = None):
    """フォルダ一覧を見て buttons を同期する。

    - フォルダ名セットが前回と同じ、かつ既存データで足りるなら何もしない（無限再検索しない）
    - 新規フォルダだけ Wikipedia + 検索する
    - force=True のときだけ全件やり直し（起動時の FORCE_RECOLLECT 用）
    """
    global _force_recollect_once, _last_folder_names

    do_force = _force_recollect_once if force is None else force
    folders = game_folders()
    names = _folder_name_set(folders)
    print(f"=== 対象フォルダ数: {len(folders)} ===", flush=True)
    if not folders:
        print("対象フォルダがありません", flush=True)
        return [], False

    saved_list = load_saved()
    saved = {b.get("folder"): b for b in saved_list}

    # 既存データがフォルダ一覧と一致していて、強制再収集でもない → スキップ
    if (
        not do_force
        and USE_EXISTING_BUTTONS
        and saved
        and names == frozenset(saved.keys())
        and all(
            saved[n].get("exe") and Path(saved[n]["exe"]).exists()
            for n in names
        )
    ):
        # 前回と同じならログも出さず静かに返す（watch からの連打対策）
        if _last_folder_names == names:
            return [saved[f.name] for f in folders if f.name in saved], False
        print("フォルダ構成に変化なし → 既存 buttons.json を使用（再検索しない）", flush=True)
        _last_folder_names = names
        # カバーパスが壊れていても、フォルダ内に cover.png 等があれば差し替え
        ordered = []
        cover_fixed = False
        for f in folders:
            b = dict(saved[f.name])
            cov = b.get("cover") or ""
            if not cov or not Path(cov).is_file():
                found = find_existing_cover(
                    Path(b["exe"]).parent if b.get("exe") else None,
                    f,
                )
                if found:
                    b["cover"] = found
                    cover_fixed = True
            ordered.append(b)
        if cover_fixed:
            save_buttons(ordered)
            return ordered, True
        return ordered, False

    # 新規・不足フォルダだけ処理対象にする
    if do_force:
        print("FORCE_RECOLLECT のため全件再収集します", flush=True)
        need = list(folders)
        saved = {}
    else:
        need = [
            f for f in folders
            if not (
                USE_EXISTING_BUTTONS
                and f.name in saved
                and saved[f.name].get("exe")
                and Path(saved[f.name]["exe"]).exists()
            )
        ]
        if not need and names == frozenset(saved.keys()):
            print("追加・不足フォルダなし → 再検索スキップ", flush=True)
            _last_folder_names = names
            ordered = [saved[f.name] for f in folders if f.name in saved]
            return ordered, False
        if not need:
            # 削除されたフォルダがあるだけ → リストを現在のフォルダに合わせて保存
            ordered = [saved[f.name] for f in folders if f.name in saved]
            save_buttons(ordered)
            _last_folder_names = names
            print(f"フォルダ削除を反映（{len(ordered)} 件）", flush=True)
            return ordered, True

    print(f"収集対象: {len(need)} / {len(folders)} 件", flush=True)

    # 第1段階: 不足分だけ Wikipedia
    print("\n=== 第1段階: Wikipediaで正確タイトルを推定（不足分のみ） ===", flush=True)
    accurate_map = {}
    for i, folder in enumerate(need, 1):
        print(f"[{i}/{len(need)}] フォルダ: {folder.name}", flush=True)
        accurate = google_find_accurate_title(folder.name, silent=False)
        accurate_map[folder.name] = accurate
        print(f"  → 推定タイトル: {accurate}", flush=True)
        time.sleep(0.6)

    print("\n=== 第2段階: 批評空間 / ローカルキャッシュで詳細検索・収集 ===", flush=True)
    s = http()
    check_egs_available(s)
    load_local_pov()

    # 既存分は維持、need だけ更新
    buttons_by_folder = dict(saved) if not do_force else {}
    changed = False
    for i, folder in enumerate(need, 1):
        print(f"[{i}/{len(need)}] {folder.name}", flush=True)
        old = saved.get(folder.name)
        try:
            accurate = accurate_map.get(folder.name, folder.name)
            rows = search(s, accurate)
            if not rows and accurate != folder.name:
                print("  推定タイトルでヒットせず、フォルダ名で再検索", flush=True)
                rows = search(s, folder.name)
            row = best(accurate, rows) or best(folder.name, rows) or {
                "title": folder.name, "id": "", "brand": "", "date": "", "median": "", "url": "",
                "cover_url": "",
            }
            src = row.get("_source", "?")
            exe = pick_exe(folder, row["title"])
            # 既存 cover（png/jpg/webp 等）があればそれを使う。FORCE 時のみ再取得
            cover = ""
            search_dirs = [folder]
            if exe:
                search_dirs.insert(0, Path(exe).parent)
            if not do_force:
                cover = find_existing_cover(*search_dirs)
                if cover:
                    print(f"  既存カバーを使用: {Path(cover).name}", flush=True)
            if not cover and (row.get("id") or row.get("cover_url")):
                dest_dir = Path(exe).parent if exe else folder
                cover = cover_of(
                    s,
                    row.get("id", ""),
                    dest_dir,
                    cover_url=row.get("cover_url", ""),
                    reuse_existing=not do_force,
                )
            buttons_by_folder[folder.name] = {
                "name": row["title"],
                "folder": folder.name,
                "exe": exe,
                "cover": cover,
                "brand": row.get("brand", ""),
                "date": row.get("date", ""),
                "median": row.get("median", ""),
                "url": row.get("url", ""),
            }
            changed = True
            print(f"  -> 結果({src}): {row['title']}", flush=True)
            time.sleep(1.0)
        except Exception as exc:
            print(f"  エラー: {folder.name} {exc}", flush=True)
            if old:
                buttons_by_folder[folder.name] = old

    # 現在のフォルダ順に並べて保存（消えたフォルダは落とす）
    buttons = [buttons_by_folder[f.name] for f in folders if f.name in buttons_by_folder]
    if changed or [b.get("folder") for b in buttons] != [b.get("folder") for b in saved_list]:
        save_buttons(buttons)
        changed = True

    _last_folder_names = names
    if do_force and force is None:
        _force_recollect_once = False
        print("FORCE_RECOLLECT 完了 → 以降はフォルダ変化時のみ再検索します", flush=True)

    return buttons, changed


def watch_buttons():
    """定期監視:
    - buttons.json / 除外リスト / カバー実体の変化 → 即反映
    - フォルダ追加 → 差分検索（除外フォルダは無視）
    """
    global _last_folder_names
    last_sig = _buttons_signature(Handler.buttons) if Handler.buttons else ""
    last_mtime = _buttons_file_mtime()
    last_excl = frozenset(load_excluded())
    while True:
        time.sleep(2)
        if TEST_LIMIT != 0:
            continue
        try:
            excl = frozenset(load_excluded())
            mtime = _buttons_file_mtime()

            # JSON or 除外リストが変わった → ディスクを正とする（再収集しない）
            if mtime != last_mtime or excl != last_excl:
                last_mtime = mtime
                last_excl = excl
                disk = [b for b in load_saved() if b.get("folder") not in excl]
                sig = _buttons_signature(disk)
                if sig != last_sig or excl != last_excl:
                    print("buttons.json / 除外の変更を検出 → 表示を即反映", flush=True)
                    Handler.buttons = disk
                    last_sig = sig
                    # 実フォルダ集合は game_folders() 基準（除外済み）
                    _last_folder_names = _folder_name_set(game_folders())
                    _bump_rev()
                    continue

            # カバー実体の上書き
            current = list(Handler.buttons) if Handler.buttons else []
            sig_now = _buttons_signature(current)
            if current and sig_now != last_sig:
                print("カバー画像の実体変化を検出 → 表示を更新", flush=True)
                Handler.buttons = current
                last_sig = sig_now
                _bump_rev()
                continue

            folders = game_folders()
            names = _folder_name_set(folders)

            if _last_folder_names is None or names != _last_folder_names:
                print("フォルダ構成の変化を検出 → 差分同期", flush=True)
                buttons, changed = sync_buttons(force=False)
                if changed or buttons is not Handler.buttons:
                    Handler.buttons = buttons
                    last_sig = _buttons_signature(buttons)
                    last_mtime = _buttons_file_mtime()
                    _bump_rev()
                continue

            if not current:
                continue
            updated, cov_changed = refresh_covers_on_disk(current, folders)
            by_name = {b.get("folder"): b for b in updated}
            ordered = [by_name[f.name] for f in folders if f.name in by_name]
            if len(ordered) != len(Handler.buttons or []) or cov_changed:
                if cov_changed:
                    print("カバー画像の更新を検出 → 表示を更新", flush=True)
                    save_buttons(ordered)
                    last_mtime = _buttons_file_mtime()
                Handler.buttons = ordered
                last_sig = _buttons_signature(ordered)
                _bump_rev()
        except Exception as exc:
            print(exc, flush=True)


def read_state():
    if not STATE.exists():
        return {"status": "idle", "app": ""}
    return json.loads(STATE.read_text(encoding="utf-8"))


def write_state(status, app=""):
    STATE.write_text(json.dumps({"status": status, "app": app}, ensure_ascii=False), encoding="utf-8")


def sunshine_close():
    user = SUNSHINE_USER or os.environ.get("SUNSHINE_USER", "")
    password = SUNSHINE_PASS or os.environ.get("SUNSHINE_PASS", "")
    if not user or not password:
        return "SUNSHINE_USER / SUNSHINE_PASS が無い"
    try:
        context = ssl._create_unverified_context()
        auth = urllib.request.HTTPBasicAuthHandler()
        auth.add_password(realm=None, uri=SUNSHINE, user=user, passwd=password)
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), auth)
        token = json.load(opener.open(SUNSHINE + "/api/csrf-token")).get("csrfToken", "")
        req = urllib.request.Request(SUNSHINE + "/api/apps/close", data=b"", method="POST")
        req.add_header("X-CSRF-Token", token)
        opener.open(req)
        return ""
    except Exception as e:
        print(f"sunshine_close error (ignored): {e}", flush=True)
        return str(e)


def _process_running(image_name: str) -> bool:
    r = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    out = (r.stdout or "").lower()
    return image_name.lower() in out


def force_stop():
    err = ""
    try:
        err = sunshine_close() or ""
    except Exception as exc:
        err = str(exc)
    if err:
        print("sunshine stop:", err, flush=True)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
         "Where-Object { $_.CommandLine -like '*runner.py*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if SELECTED.exists():
        name = SELECTED.read_text(encoding="utf-8").strip()
        for button in Handler.buttons:
            if button.get("name") == name and button.get("exe"):
                subprocess.run(
                    ["taskkill", "/IM", Path(button["exe"]).name, "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
    subprocess.run(["taskkill", "/IM", "streamer.exe", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    write_state("stopped")


def _selected_game_exe_name():
    if not SELECTED.exists():
        return ""
    name = SELECTED.read_text(encoding="utf-8").strip()
    for button in Handler.buttons:
        if button.get("name") == name and button.get("exe"):
            return Path(button["exe"]).name
    return ""


def watch_session():
    grace_until = 0.0
    seen_streamer = False
    while True:
        time.sleep(0.5)
        try:
            st = read_state()
            status = st.get("status", "idle")
            if status != "starting":
                grace_until = 0.0
                seen_streamer = False
                continue

            now = time.time()
            if grace_until == 0.0:
                grace_until = now + 8.0
                seen_streamer = False
                continue

            streamer_alive = _process_running("streamer.exe")
            game_name = _selected_game_exe_name()
            game_alive = _process_running(game_name) if game_name else False

            if streamer_alive:
                seen_streamer = True

            if seen_streamer and not streamer_alive:
                print("session ended (streamer gone) -> stopped", flush=True)
                try:
                    force_stop()
                except Exception as exc:
                    print("watch_session force_stop:", exc, flush=True)
                    write_state("stopped")
                seen_streamer = False
                grace_until = 0.0
                continue

            if not streamer_alive and not game_alive:
                if now < grace_until and not seen_streamer:
                    continue
                reason = "no streamer/game" if seen_streamer else "startup timeout (never alive)"
                print(f"session ended ({reason}) -> stopped", flush=True)
                try:
                    force_stop()
                except Exception as exc:
                    print("watch_session force_stop:", exc, flush=True)
                    write_state("stopped")
                seen_streamer = False
                grace_until = 0.0
        except Exception as exc:
            print("watch_session:", exc, flush=True)


def page(buttons):
    """初期HTML。以降の更新はクライアント側で /buttons を見て差分反映する。"""
    cards = []
    for i, b in enumerate(buttons):
        cov = b.get("cover") or ""
        stamp = _cover_stamp(cov) if cov else "0"
        if cov:
            img = f'<img src="/cover/{i}?v={stamp}" alt="">'
        else:
            img = '<div class="no">no image</div>'
        name_esc = html.escape(b.get("name", ""), quote=True)
        brand_esc = html.escape(b.get("brand", ""), quote=True)
        cards.append(
            f'<button class="card" data-idx="{i}">{img}'
            f'<span>{name_esc}</span><small>{brand_esc}</small></button>'
        )
    grid = "\n".join(cards)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>eroge</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<style>
html,body {{ margin:0; height:100%; background:#17141f; color:#eee; font-family:sans-serif; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:16px; padding:16px; }}
.card {{ background:#221c2e; border:0; color:#eee; border-radius:10px; padding:0 0 10px; cursor:pointer; text-align:left; }}
.card img,.no {{ width:100%; height:240px; object-fit:cover; background:#111; }}
.card span {{ display:block; padding:8px 10px 0; font-weight:700; }}
.card small {{ display:block; padding:2px 10px 0; color:#aaa; }}
iframe {{ width:100%; height:100%; border:0; background:#000; }}
body.picking iframe {{ display:none; }}
body.streaming .grid, body.streaming .card {{ display:none; }}
</style></head>
<body class="picking">
<div class="grid" id="grid">{grid}</div>
<iframe id="view" src="about:blank"></iframe>
<script>
const view = document.getElementById("view");
const gridEl = document.getElementById("grid");
let streaming = false;
let stopHits = 0;
let rev = {_buttons_rev};

function bindCards() {{
  for (const b of document.querySelectorAll(".card")) {{
    b.onclick = async () => {{
      const idx = b.dataset.idx;
      const res = await fetch("/select", {{ method:"POST", body:String(idx) }});
      const data = await res.json();
      if (!data.ok) return;
      streaming = true;
      stopHits = 0;
      document.body.className = "streaming";
      view.src = data.url;
    }};
  }}
}}
bindCards();

function showPicker() {{
  streaming = false;
  stopHits = 0;
  try {{ view.src = "about:blank"; }} catch(e) {{}}
  document.body.className = "picking";
}}

function rebuildGrid(items) {{
  const frag = document.createDocumentFragment();
  for (const it of items) {{
    const btn = document.createElement("button");
    btn.className = "card";
    btn.dataset.idx = String(it.idx);
    if (it.has_cover) {{
      const img = document.createElement("img");
      img.src = "/cover/" + it.idx + "?v=" + encodeURIComponent(it.stamp || "0");
      img.alt = "";
      btn.appendChild(img);
    }} else {{
      const d = document.createElement("div");
      d.className = "no";
      d.textContent = "no image";
      btn.appendChild(d);
    }}
    const sp = document.createElement("span");
    sp.textContent = it.name || "";
    const sm = document.createElement("small");
    sm.textContent = it.brand || "";
    btn.appendChild(sp);
    btn.appendChild(sm);
    frag.appendChild(btn);
  }}
  gridEl.innerHTML = "";
  gridEl.appendChild(frag);
  bindCards();
}}

setInterval(async () => {{
  if (streaming) {{
    try {{
      const data = await (await fetch("/state")).json();
      if (data.status === "stopped") {{
        stopHits += 1;
        if (stopHits >= 2) showPicker();
      }} else {{
        stopHits = 0;
      }}
    }} catch (e) {{}}
    return;
  }}
  try {{
    const data = await (await fetch("/buttons")).json();
    if (data.rev !== rev && Array.isArray(data.items)) {{
      rev = data.rev;
      rebuildGrid(data.items);
    }}
  }} catch (e) {{}}
}}, 1000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    buttons = []

    def log_message(self, fmt, *args):
        return

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            data = page(self.buttons).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self._send_cors()
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/state":
            self._json(200, read_state())
            return
        if path == "/buttons":
            # クライアントが差分更新できるよう詳細を返す
            items = []
            for i, b in enumerate(self.buttons):
                cov = b.get("cover") or ""
                stamp = _cover_stamp(cov) if cov else "0"
                items.append({
                    "idx": i,
                    "name": b.get("name") or "",
                    "brand": b.get("brand") or "",
                    "has_cover": bool(cov),
                    "stamp": stamp,
                })
            self._json(200, {
                "rev": _buttons_rev,
                "count": len(self.buttons),
                "items": items,
            })
            return
        if path.startswith("/cover/"):
            try:
                idx = int(path.rsplit("/", 1)[-1])
                btn = self.buttons[idx]
                cover_path = Path(btn.get("cover") or "")
                if not _is_usable_cover(cover_path):
                    # 登録パスが死んでいても、フォルダ内の cover.* をその場で拾う
                    folder_name = btn.get("folder") or ""
                    exe = btn.get("exe") or ""
                    recovered = find_existing_cover(
                        Path(exe).parent if exe else None,
                        HERE / folder_name if folder_name else None,
                    )
                    # ゲーム本体フォルダ（exeの親の親など）も一応
                    if not recovered and exe:
                        recovered = find_existing_cover(Path(exe).parent.parent)
                    if recovered:
                        cover_path = Path(recovered)
                        btn["cover"] = recovered
                    else:
                        self.send_error(404)
                        return
                data = cover_path.read_bytes()
            except Exception:
                self.send_error(404)
                return
            ext = cover_path.suffix.casefold()
            ctype = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
                ".avif": "image/avif",
            }.get(ext, "image/jpeg")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self._send_cors()
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        n = int(self.headers.get("Content-Length", "0"))
        raw = unquote(self.rfile.read(n).decode("utf-8")).strip()
        if path == "/select":
            button = None
            try:
                idx = int(raw)
                if 0 <= idx < len(self.buttons):
                    button = self.buttons[idx]
            except ValueError:
                button = next((b for b in self.buttons if b.get("name") == raw), None)
            if not button:
                self._json(400, {"ok": False, "error": "unknown"})
                return
            name = button["name"]
            SELECTED.write_text(name, encoding="utf-8")
            write_state("starting", name)
            print(f"select -> {name}  (exe={button.get('exe','')})", flush=True)

            host_header = self.headers.get("Host", "127.0.0.1:8765")
            host = host_header.split(":")[0]

            is_local = (
                host in ("127.0.0.1", "localhost", "192.168.1.12")
                or host.startswith("192.168.")
                or host.startswith("10.")
                or host.startswith("172.")
            )

            if is_local:
                stream_url = f"http://{host}:8090/stream.html?hostId={HOST_ID}&appId={APP_ID}"
            else:
                # 外部アクセス時は公開IP + 外部ポート
                stream_url = f"http://14.8.155.32:{EXTERNAL_STREAM_PORT}/stream.html?hostId={HOST_ID}&appId={APP_ID}"

            print(f"  stream_url = {stream_url}", flush=True)
            self._json(200, {"ok": True, "url": stream_url})
            return
        if path == "/exit":
            force_stop()
            self._json(200, {"ok": True})
            return
        self.send_error(404)


def kill_existing_web_server():
    print("既存の web-server.exe を終了しています...", flush=True)
    subprocess.run(
        ["taskkill", "/IM", "web-server.exe", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)


def start_web():
    global web_proc
    if not WEB.exists():
        raise SystemExit(f"web-server.exe が無い: {WEB}")
    env = os.environ.copy()
    env["BIND_ADDRESS"] = BIND_ADDRESS
    web_proc = subprocess.Popen([str(WEB)], cwd=str(HERE), env=env)


def stop_web():
    if web_proc and web_proc.poll() is None:
        subprocess.run(
            ["taskkill", "/pid", str(web_proc.pid), "/t", "/f"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    Handler.buttons, _ = sync_buttons()
    _bump_rev()  # 初回表示用
    threading.Thread(target=watch_buttons, daemon=True).start()
    threading.Thread(target=watch_session, daemon=True).start()

    kill_existing_web_server()

    start_web()
    print(f"http://0.0.0.0:{PORT}/  (他端末からはこのPCのIP:{PORT}/ でアクセス)", flush=True)
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    finally:
        stop_web()
