#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
エロ漫画フォルダの更新日時インデックスを生成する。
Shelf の _shelf_mtimes.json として配置すると、
クライアントは HEAD/list をほぼせずに「更新日時順」ソートできる。

使い方:
  python generate_shelf_mtimes.py
  python generate_shelf_mtimes.py "W:\\エロ漫画"
  python generate_shelf_mtimes.py "./エロ漫画"

- 指定が無ければカレントの「エロ漫画」または config 想定の相対パスを試す
- 出力: <対象ルート>/_shelf_mtimes.json
- 既にあれば上書き（再生成）
"""

from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

def folder_mtime(path: Path) -> float | None:
    """フォルダ自身 or 中身の最新 mtime を秒で返す。"""
    try:
        best = path.stat().st_mtime
    except OSError:
        return None
    try:
        # 直下のファイルだけ見れば十分なことが多い（深い再帰は避ける）
        with os.scandir(path) as it:
            for ent in it:
                try:
                    st = ent.stat(follow_symlinks=False)
                    if st.st_mtime > best:
                        best = st.st_mtime
                except OSError:
                    continue
    except OSError:
        pass
    return best

def scan(root: Path) -> dict[str, int]:
    folders: dict[str, int] = {}
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError as e:
        print(f"[ERROR] 読めません: {root} ({e})")
        return folders

    for ent in entries:
        if not ent.is_dir(follow_symlinks=False):
            continue
        name = ent.name
        if name.startswith(".") or name == "エロ漫画視聴ソフト":
            continue
        mt = folder_mtime(Path(ent.path))
        if mt is None:
            continue
        # クライアントは href 形式（末尾スラッシュ付き）を優先して探す
        key = name + "/"
        folders[key] = int(mt * 1000)  # JS の Date と同じ ms
        # スラッシュ無しも一応入れておく
        folders[name] = folders[key]
    return folders

def main() -> int:
    candidates = []
    if len(sys.argv) >= 2:
        candidates.append(Path(sys.argv[1]))
    else:
        # カレントからよくある配置を試す
        here = Path.cwd()
        candidates.extend([
            here / "エロ漫画",
            here / "manga",
            here.parent / "エロ漫画",
            Path(r"W:\エロ漫画"),
            Path(r"W:\エロ漫画"),
        ])

    root = None
    for c in candidates:
        try:
            if c.is_dir():
                root = c.resolve()
                break
        except OSError:
            continue

    if root is None:
        print("対象フォルダが見つかりません。")
        print("  使い方: python generate_shelf_mtimes.py \"W:\\\\エロ漫画\"")
        return 1

    print(f"スキャン中: {root}")
    t0 = time.perf_counter()
    folders = scan(root)
    elapsed = time.perf_counter() - t0

    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(root),
        "count": len(folders) // 2,  # name + name/ の二重登録
        "folders": folders,
    }

    out_path = root / "_shelf_mtimes.json"
    tmp = out_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    tmp.replace(out_path)

    print(f"完了: {out_path}")
    print(f"  フォルダ数: {out['count']}")
    print(f"  所要時間: {elapsed:.2f}s")
    print("Shelf を再読み込みすると「更新日時順」がほぼ即座になります。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
