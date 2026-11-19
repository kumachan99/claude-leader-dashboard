#!/usr/bin/env python3
"""leader.json に Claude の返事・項目の状態・動いたことを書く（JSON を手で壊さないための道具）。

使い方:
  python3 -I reply.py <作業フォルダー> --qid Q1 --text "予約した。確認のメールは届いている"
  python3 -I reply.py <作業フォルダー> --status trip-inn done
  python3 -I reply.py <作業フォルダー> --log "宿の予約を済ませた"
  python3 -I reply.py <作業フォルダー> --headline "残りの判断待ちは2件です"
  python3 -I reply.py <作業フォルダー> --project blog --qid blog-publish-ok --text "直した" --log "本文を直した"
  （まとめて1回でもよい。--qid/--text の組、--status、--log は何度でも付けられる）

--project を付けると、--log の行にプロジェクトの id が付く（担当のエージェントが自分の動きを書くとき）。
項目の状態は、items/<project>.json の項目なら item.py status で変える（--status は items.json の項目向け）。

leader.json の形:
  {"headline": "...", "status": {"<項目の id>": "decide|running|done"},
   "replies": {"<問いの id>": "..."}, "log": [{"ts": "...", "text": "...", "project"?: "..."}]}
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import leaderlib  # noqa: E402

STATUSES = leaderlib.STATUSES
now = leaderlib.now


def known_ids(work):
    """items.json と items/*.json にある項目の id と問いの id（どれも読めなければ None）。"""
    data, errors, _, files = leaderlib.load_items(work)
    if not files or errors:
        return None, None
    items, qowner = leaderlib.item_index(data)
    return set(items), set(qowner)


def main():
    ap = argparse.ArgumentParser(description="leader.json に返事・状態・動いたことを書く")
    ap.add_argument("dir", help="作業フォルダー")
    ap.add_argument("--qid", action="append", default=[], help="返事を付ける問いの id")
    ap.add_argument("--text", action="append", default=[], help="問いへの返事（--qid と同じ数だけ、同じ順に）。空文字で返事を消す")
    ap.add_argument("--status", nargs=2, action="append", metavar=("ITEM", "STATUS"), default=[],
                    help="項目の状態を変える（decide / running / done）")
    ap.add_argument("--log", action="append", default=[], help="「動いたこと」に1行足す")
    ap.add_argument("--headline", help="全体のタブの見出しを変える")
    ap.add_argument("--project", help="--log の行に付けるプロジェクトの id（担当が書くとき）")
    a = ap.parse_args()

    if len(a.qid) != len(a.text):
        ap.error("--qid と --text は同じ数だけ付ける")
    for item, st in a.status:
        if st not in STATUSES:
            ap.error(f"状態は {' / '.join(STATUSES)} のどれか: {st}")
    if not (a.qid or a.status or a.log or a.headline is not None):
        ap.error("書くことが無い（--qid/--text、--status、--log、--headline のどれかを付ける）")

    work = pathlib.Path(a.dir).expanduser()
    if not work.is_dir():
        sys.exit(f"作業フォルダーが無い: {work}")
    items, qids = known_ids(work)
    for qid in a.qid:
        if qids is not None and qid not in qids:
            print(f"注意: 項目のファイルに問い {qid} が無い（書くことは書く）", file=sys.stderr)
    for item, _ in a.status:
        if items is not None and item not in items:
            print(f"注意: 項目のファイルに項目 {item} が無い（書くことは書く）", file=sys.stderr)

    path = work / "leader.json"
    with leaderlib.leader_lock(work):
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as e:
                sys.exit(f"leader.json が壊れている（直してからやり直す）: {e}")
        if not isinstance(data, dict):
            sys.exit("leader.json の一番外がオブジェクトでない")
        for k, empty in (("status", {}), ("replies", {}), ("log", [])):
            if not isinstance(data.get(k), type(empty)):
                data[k] = empty

        done = []
        for qid, text in zip(a.qid, a.text):
            if text:
                data["replies"][qid] = text
                done.append(f"返事 {qid}")
            else:
                data["replies"].pop(qid, None)
                done.append(f"返事を消した {qid}")
        for item, st in a.status:
            data["status"][item] = st
            done.append(f"状態 {item}={st}")
        for text in a.log:
            entry = {"ts": now(), "text": text}
            if a.project:
                entry["project"] = a.project
            data["log"].append(entry)
            done.append("ログ 1行")
        if a.headline is not None:
            data["headline"] = a.headline
            done.append("見出し")

        leaderlib.write_json(path, data)
    print("書いた: " + "、".join(done))


if __name__ == "__main__":
    main()
