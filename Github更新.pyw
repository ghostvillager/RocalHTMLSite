# Github更新.pyw
# このファイルがあるフォルダをGitHubへ更新する
# ネットワークドライブ対応

import os
import subprocess
import tkinter as tk
from tkinter import simpledialog, messagebox


MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024


def to_git_path(path):
    return os.path.abspath(path).replace("\\", "/")


def run_git(cmd, cwd, timeout=300):
    try:
        result = subprocess.run(
            ["git"] + cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "タイムアウトしました（ネットワークが遅い可能性があります）"
    except Exception as e:
        return False, str(e)


def ensure_safe_directory(folder):
    git_path = to_git_path(folder)
    candidates = [git_path, git_path + "/"]
    if git_path.startswith("//"):
        unc_form = "%(prefix)/" + git_path
        candidates.append(unc_form)
        candidates.append(unc_form + "/")
    home = os.path.expanduser("~")
    for c in candidates:
        run_git(
            ["config", "--global", "--add", "safe.directory", c],
            cwd=home,
        )


def update_gitignore_for_large_files(folder):
    gitignore_path = os.path.join(folder, ".gitignore")
    existing_lines = []
    if os.path.isfile(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                existing_lines = [line.strip() for line in f.readlines() if line.strip()]
        except Exception:
            existing_lines = []
    existing_set = set(existing_lines)
    large_files = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for filename in files:
            file_path = os.path.join(root, filename)
            if os.path.abspath(file_path) == os.path.abspath(gitignore_path):
                continue
            try:
                file_size = os.path.getsize(file_path)
            except (OSError, PermissionError):
                continue
            if file_size >= MAX_FILE_SIZE:
                relative_path = os.path.relpath(file_path, folder).replace("\\", "/")
                large_files.append(("/" + relative_path, file_size))
    new_entries = [p for p, _ in large_files if p not in existing_set]
    if new_entries:
        try:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                try:
                    with open(gitignore_path, "rb") as check_file:
                        content = check_file.read()
                    if content and not content.endswith(b"\n"):
                        f.write("\n")
                except Exception:
                    pass
                for entry in new_entries:
                    f.write(entry + "\n")
        except Exception as e:
            raise Exception(f".gitignoreの作成に失敗しました:\n{e}")
    return large_files


def format_large_files(large_files):
    if not large_files:
        return ""
    return "\n".join(
        f"{path} ({size / (1024 * 1024):.1f} MB)"
        for path, size in large_files
    )


def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.normpath(folder)

    if not os.path.isdir(folder):
        messagebox.showerror("エラー", f"フォルダが存在しません:\n{folder}")
        return

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    ensure_safe_directory(folder)

    ok, out = run_git(["rev-parse", "--is-inside-work-tree"], folder)
    if not ok:
        if "dubious" in out.lower() or "safe.directory" in out.lower():
            ensure_safe_directory(folder)
            ok, out = run_git(["rev-parse", "--is-inside-work-tree"], folder)
        if not ok:
            messagebox.showerror(
                "エラー",
                "このフォルダはGitリポジトリではありません。\n"
                "先に upload_to_git で登録してください。\n\n"
                f"{out}"
            )
            return

    try:
        large_files = update_gitignore_for_large_files(folder)
    except Exception as e:
        messagebox.showerror("エラー", str(e))
        return

    ok, out = run_git(["add", "."], folder)
    if not ok:
        if "dubious" in out.lower() or "safe.directory" in out.lower():
            ensure_safe_directory(folder)
            ok, out = run_git(["add", "."], folder)
        if not ok:
            messagebox.showerror("エラー", f"git add 失敗:\n{out}")
            return

    ok, status = run_git(["status", "--porcelain"], folder)
    if not status.strip():
        if large_files:
            messagebox.showinfo(
                "Git更新",
                "変更がありません。\n\n"
                f"{MAX_FILE_SIZE_MB}MB以上のファイルは\n"
                "GitHubから除外されています。\n\n"
                f"{format_large_files(large_files)}"
            )
        else:
            messagebox.showinfo("Git更新", "変更がありません。")
        return

    msg = simpledialog.askstring(
        "Git更新",
        "コミットメッセージを入力してください:\n"
        "（空欄の場合は「Update」になります）",
        parent=root
    )
    if msg is None:
        return
    if not msg.strip():
        msg = "Update"

    try:
        ok, out = run_git(["commit", "-m", msg], folder)
        if not ok:
            raise Exception(f"git commit 失敗:\n{out}")

        ok, out = run_git(["push"], folder)
        if not ok:
            raise Exception(
                f"git push 失敗:\n{out}\n\n"
                "※ 認証エラーの場合は、GitHubの設定を確認してください。"
            )

        if large_files:
            messagebox.showinfo(
                "完了",
                "更新が完了しました！\n\n"
                f"{MAX_FILE_SIZE_MB}MB以上のファイルは\n"
                "GitHubから除外されています。\n\n"
                f"{format_large_files(large_files)}"
            )
        else:
            messagebox.showinfo("完了", "更新が完了しました！")

    except Exception as e:
        messagebox.showerror("エラー", str(e))


if __name__ == "__main__":
    main()
