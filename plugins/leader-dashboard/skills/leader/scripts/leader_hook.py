#!/usr/bin/env python3
"""leader-dashboard のフック（hooks/hooks.json から呼ぶ）。標準入力にフックの入力の JSON が来る。

  leader_hook.py session-start   その日の作業フォルダーがあれば、再開に要ることを数行で出す
  leader_hook.py prompt          振り分けていない答えがあれば、1行だけ出す

作業フォルダーの探し方は leaderlib.find_workdir（LEADER_DASHBOARD_DIR → <cwd>/.leader/<今日>
→ <CLAUDE_PROJECT_DIR>/.leader/<今日>）。無ければ何も出さずに終わる。
server.json の session が今のセッションと違い、そのサーバーが動いているときは、
別のセッションがリーダーをしているとみなし、prompt では黙る。
どんなときも終了コード 0 で終わる（会話を止めない）。
"""
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import leaderlib  # noqa: E402


def show(path, cwd):
    """cwd の中なら相対パスで、外なら絶対パスで出す。"""
    p = pathlib.Path(path).absolute()
    try:
        return str(p.relative_to(pathlib.Path(cwd).absolute())) or "."
    except ValueError:
        return str(p)


def emit(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}},
                     ensure_ascii=False))


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        inp = {}
    if not isinstance(inp, dict):
        inp = {}
    cwd = inp.get("cwd") or os.getcwd()
    work = leaderlib.find_workdir(cwd, os.environ.get("CLAUDE_PROJECT_DIR"))
    if work is None:
        return
    rel = show(work, cwd)
    total = leaderlib.count_lines(work / "decisions.jsonl")
    pending = max(0, total - leaderlib.read_cursor(work))
    srv = leaderlib.server_info(work)
    session = str(inp.get("session_id") or "")
    other = bool(srv and srv["alive"] and srv.get("session") and session and srv["session"] != session)
    route = f"python3 -I {HERE / 'route.py'} {rel}"

    if event == "prompt":
        if pending and not other:
            emit("UserPromptSubmit", f"ダッシュボードに未処理の答えが {pending} 件（route.py {rel} で読める）")
        return

    if event != "session-start":
        return
    if other:
        emit("SessionStart", f"[leader-dashboard] {rel} では別のセッションがリーダーをしている"
                             f"（{srv.get('url', '')}）。答えの振り分けはそちらに任せる")
        return

    lines = [f"[leader-dashboard] 今日のリーダーの作業フォルダー: {rel}（手順はスキル leader-dashboard:leader）"]
    if srv and srv["alive"]:
        lines.append(f"- ダッシュボード: {srv.get('url', '')}（動いている）")
    elif srv:
        lines.append(f"- ダッシュボード: 止まっている（前は {srv.get('url', '')}）。続けるなら server.py を起動し直す")
    else:
        lines.append("- ダッシュボード: 起動の記録が無い（続けるなら server.py を起動する）")

    data, errors, _, _ = leaderlib.load_items(work)
    leader = leaderlib.load_leader(work)
    done_map = leaderlib.user_done_map(leaderlib.read_decisions(work))
    labels = {str(p["id"]): str(p.get("label") or p["id"]) for p in data["projects"]}
    counts = {}
    for it in data["items"]:
        if isinstance(it, dict) and leaderlib.effective_status(it, leader, done_map) == "decide":
            pid = str(it.get("project") or "other")
            counts[pid] = counts.get(pid, 0) + 1
    if counts:
        order = [p for p in labels if p in counts] + [p for p in counts if p not in labels]
        parts = [f"{labels.get(p, p)} {counts[p]}" for p in order]
        lines.append(f"- 判断待ち: {'・'.join(parts)}（計 {sum(counts.values())}）")
    else:
        lines.append("- 判断待ち: 無い")
    if errors:
        lines.append(f"- 項目のファイルに読めないものがある: {errors[0]}")
    lines.append(f"- 未処理の答え: {pending} 件" + (f"（{route} で読む）" if pending else ""))

    hd = work / "handoff"
    memos = sorted(p.name for p in hd.glob("*.md")) if hd.is_dir() else []
    if memos:
        lines.append(f"- 引き継ぎのメモ: {show(hd, cwd)}/（{'・'.join(memos)}）。"
                     "再開するときはこれだけ読む。下書きの本文や長いファイルは読まない")
    else:
        lines.append(f"- 引き継ぎのメモ: まだ無い（{show(hd, cwd)}/<project>.md に担当が書く）")

    roster, _ = leaderlib.read_json(work / "roster.json", {})
    n_lead = len(roster) if isinstance(roster, dict) else 0
    if n_lead:
        if inp.get("source") == "compact":
            lines.append(f"- 担当: 名簿に {n_lead} 本（{route} --who）。圧縮の後も、そのまま SendMessage で頼める")
        else:
            lines.append(f"- 担当: 名簿に {n_lead} 本。ID は前の会話のもので、使えないことがある。"
                         "届かなければ引き継ぎのメモから担当を出し直し、--roster で書き直す")
    emit("SessionStart", "\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # フックの失敗で会話を止めない
        pass
    sys.exit(0)
