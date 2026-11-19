#!/usr/bin/env python3
"""ダッシュボードの答えを、プロジェクトの担当ごとにまとめて短く出す（リーダーが振り分けるための道具）。

使い方（<dir> は作業フォルダー）:
  route.py <dir>                      まだ振り分けていない答え（cursor より後）を、プロジェクトごとに出す
  route.py <dir> --ack                出して、出した所まで振り分け済みにする（cursor を進める）
  route.py <dir> --ack 14             14 行目まで振り分け済みにする（出さない）
  route.py <dir> --project blog --from 12 --full
                                      1つのプロジェクトの答えを、メモを切らずに出す（担当が読む）
  route.py <dir> --roster blog --agent <エージェントの ID> --summary "下書き2本を画面に出した"
                                      担当の名簿を書く（--agent を省くと要約だけ直す。--remove で消す）
  route.py <dir> --who                名簿を出す

答えの行: 問いへの答え（#行番号 問いの id「選択」メモ: …）、一言（#行番号 一言: …）、
画面の完了ボタン（#行番号 完了にした：<題> / 未完了に戻した：<題>）。どれも項目のプロジェクトにまとめる。
cursor: 作業フォルダーの cursor（振り分け済みの decisions.jsonl の行数）。
roster.json: {"<project>": {"agent", "started", "summary", "updated"}}。
エージェントの ID はそのセッションの中でだけ使える。別のセッションでは handoff/<project>.md から担当を出し直す。
"""
import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import leaderlib  # noqa: E402

NOTE_MAX = 60  # 振り分けの一覧でメモを切る長さ（字）
GLOBAL = "（全体）"


def load_roster(work):
    data, err = leaderlib.read_json(work / "roster.json", {})
    if err:
        sys.exit(f"route.py: {err}（直してからやり直す）")
    return data if isinstance(data, dict) else {}


def clip(text, full):
    text = " ".join(str(text or "").split())
    if full or len(text) <= NOTE_MAX:
        return text
    return text[:NOTE_MAX] + "…"


def describe(n, d, full, items):
    """答え1件を1行にする。"""
    if d.get("qid") == leaderlib.DONE_QID:
        it = items.get(str(d.get("item"))) or {}
        title = clip(it.get("title") or d.get("item"), False)
        what = "完了にした" if d.get("done") is True else "未完了に戻した"
        return f"#{n} {what}：{title}（{d.get('item')}）"
    note = clip(d.get("note"), full)
    cut = note.endswith("…") and not full
    if d.get("qid") == "message":
        line = f"#{n} 一言: {note}"
    else:
        line = f"#{n} {d.get('qid')}「{d.get('choice', '')}」" + (f" メモ: {note}" if note else "")
    return line + ("（全文は --full）" if cut else "")


def group_by_project(entries, data):
    items, qowner = leaderlib.item_index(data)
    groups = {}
    for n, d in entries:
        iid = d.get("item") or qowner.get(str(d.get("qid")))
        it = items.get(str(iid)) if iid else None
        pid = str(it.get("project") or "other") if it else GLOBAL
        groups.setdefault(pid, []).append((n, d, it is None and bool(iid)))
    return groups


def lead_line(pid, label, roster):
    if pid == GLOBAL:
        return f"[{GLOBAL}] → リーダー"
    r = roster.get(pid)
    head = f"[{pid}] {label}" if label and label != pid else f"[{pid}]"
    if isinstance(r, dict) and r.get("agent"):
        started = str(r.get("started") or "")[11:16]  # "YYYY-MM-DD HH:MM:SS" の HH:MM
        summary = clip(r.get("summary"), False)
        tail = "・".join(x for x in (f"{started} から" if started else "", f"最後: {summary}" if summary else "") if x)
        return f"{head} → 担当 {r['agent']}" + (f"（{tail}）" if tail else "")
    return f"{head} → 担当なし（リーダーが扱うか、担当を出す）"


def cmd_route(a, work):
    total = leaderlib.count_lines(work / "decisions.jsonl")
    cursor = leaderlib.read_cursor(work)
    entries = leaderlib.read_decisions(work, a.start or cursor + 1)
    data = leaderlib.load_items(work)[0]
    labels = {str(p["id"]): str(p.get("label") or p["id"]) for p in data["projects"]}
    groups = group_by_project(entries, data)
    items = leaderlib.item_index(data)[0]
    if a.project:
        groups = {k: v for k, v in groups.items() if k == a.project}
    roster = load_roster(work)

    shown = [n for g in groups.values() for n, _, _ in g]
    if not shown:
        scope = f"プロジェクト {a.project} の" if a.project else "振り分けていない"
        print(f"{scope}答えは無い（decisions.jsonl {total} 行・振り分け済み {min(cursor, total)} 行）")
    else:
        rng = f"{min(shown)}〜{max(shown)}" if len(shown) > 1 else f"{shown[0]}"
        print(f"答え {len(shown)} 件（decisions.jsonl の {rng} 行目）")
        order = [p for p in labels if p in groups] + [p for p in groups if p not in labels and p != GLOBAL]
        if GLOBAL in groups:
            order.append(GLOBAL)
        for pid in order:
            print(lead_line(pid, labels.get(pid), roster))
            for n, d, missing in groups[pid]:
                extra = f"（項目 {d.get('item')} が見つからない）" if missing else ""
                print("  " + describe(n, d, a.full, items) + extra)

    if a.ack is not None:
        # 出した所まで進める（出した後に届いた答えは次に回す）
        with leaderlib.leader_lock(work):
            upto = max([leaderlib.read_cursor(work)] + shown)
            leaderlib.write_text(work / "cursor", f"{upto}\n")
        print(f"振り分け済み: {upto} 行目まで（残り {max(0, total - upto)} 件）")
    elif shown and not a.project and not a.start:
        print("（振り分けたら --ack で振り分け済みにする）")


def cmd_ack_only(a, work):
    total = leaderlib.count_lines(work / "decisions.jsonl")
    upto = min(a.ack, total)
    with leaderlib.leader_lock(work):
        leaderlib.write_text(work / "cursor", f"{max(0, upto)}\n")
    print(f"振り分け済み: {upto} 行目まで（残り {max(0, total - upto)} 件）")


def cmd_roster(a, work):
    pid = a.roster
    if not leaderlib.ID_RE.match(pid):
        sys.exit(f"route.py: プロジェクトの id は英数字・-・_ で: {pid!r}")
    with leaderlib.leader_lock(work):
        roster = load_roster(work)
        if a.remove:
            roster.pop(pid, None)
            what = "消した"
        else:
            r = roster.get(pid) if isinstance(roster.get(pid), dict) else {}
            if a.agent:
                if r.get("agent") != a.agent:
                    r["started"] = leaderlib.now()
                r["agent"] = a.agent
            elif not r.get("agent"):
                sys.exit("route.py: 名簿に無いプロジェクトには --agent が要る")
            if a.summary is not None:
                r["summary"] = " ".join(a.summary.split())[:200]
            r["updated"] = leaderlib.now()
            roster[pid] = r
            what = f"担当 {r['agent']}"
        leaderlib.write_json(work / "roster.json", roster)
    print(f"名簿: {pid} の{what}")


def cmd_who(a, work):
    roster = load_roster(work)
    if not roster:
        print("名簿は空（担当を出したら --roster で書く）")
        return
    data = leaderlib.load_items(work)[0]
    labels = {str(p["id"]): str(p.get("label") or p["id"]) for p in data["projects"]}
    for pid, r in roster.items():
        if isinstance(r, dict):
            print(lead_line(pid, labels.get(pid), roster))


def main():
    ap = argparse.ArgumentParser(description="ダッシュボードの答えを担当ごとに振り分ける",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("dir", help="作業フォルダー")
    ap.add_argument("--ack", nargs="?", type=int, const=-1, metavar="N",
                    help="振り分け済みにする（N を省くと、いま出した所まで）")
    ap.add_argument("--project", help="1つのプロジェクトの答えだけを出す")
    ap.add_argument("--from", dest="start", type=int, metavar="N", help="N 行目から出す（cursor を無視する）")
    ap.add_argument("--full", action="store_true", help="メモを切らずに出す")
    ap.add_argument("--roster", metavar="PROJECT", help="名簿を書く")
    ap.add_argument("--agent", help="担当のエージェントの ID（--roster と）")
    ap.add_argument("--summary", help="担当の最後の要約（--roster と）")
    ap.add_argument("--remove", action="store_true", help="名簿から消す（--roster と）")
    ap.add_argument("--who", action="store_true", help="名簿を出す")
    a = ap.parse_args()

    work = pathlib.Path(a.dir).expanduser()
    if not work.is_dir():
        sys.exit(f"route.py: 作業フォルダーが無い: {work}")
    if a.roster:
        return cmd_roster(a, work)
    if a.who:
        return cmd_who(a, work)
    if a.ack is not None and (a.project or a.start):
        sys.exit("route.py: --ack は --project・--from と一緒に使えない")
    if a.ack is not None and a.ack >= 0:
        return cmd_ack_only(a, work)
    cmd_route(a, work)


if __name__ == "__main__":
    main()
