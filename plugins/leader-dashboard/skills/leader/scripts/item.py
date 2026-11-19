#!/usr/bin/env python3
"""items/<project>.json の項目を書き換える（担当のエージェントが JSON を手で壊さないための道具）。

使い方（<dir> は作業フォルダー、<p> はプロジェクトの id）:
  item.py <dir> show <p>                                     項目と問いと答えを短く出す
  item.py <dir> project <p> --label "ブログ"                  ファイルを作る・タブの名前を変える
  item.py <dir> lead <p> working "下書きを2本作っている"        担当の状態（working / idle / waiting）と1行
  item.py <dir> put <p> <item> --title "記事を公開する" --group "今週" --summary "1行目" --summary "2行目"
  item.py <dir> status <p> <item> done                      状態（decide / running / done）
  item.py <dir> ask <p> <item> <qid> "この本文で公開する？" --option "公開してよい" --option "保留"
  item.py <dir> draft <p> <item> --label 本文 --file <dir>/drafts/<item>.md --heading "## 本文" --whole
  item.py <dir> ask <p> <item> <qid> --remove / draft <p> <item> --label 本文 --remove

put の欄:
  --title / --group / --status / --urgent / --not-urgent
  --summary（3行まで。付けると今の行を置き換える）/ --details / --link LABEL URL / --svg-file F
  --clear summary|details|links|drafts|questions|svg_file   欄を空にする
  --json FILE（- で標準入力）  JSON のオブジェクトの欄をそのまま重ねる（svg など長いもの向け）
  新しい項目の状態は running。問いを足すと decide にする（--keep-status で変えない）。

ファイルの形: {"project": {"id", "label", "lead"?}, "items": [...]}。
lead は {"status": "working|idle|waiting", "summary": "1行", "updated": "MM/DD HH:MM"}。
書くときはロックを取り、一時ファイルに書いてから置き換える。壊れたファイルは上書きしない。
状態を変えると、leader.json に残っている同じ項目の状態（reply.py --status）を消す（こちらを正にする）。
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import leaderlib  # noqa: E402

FIELDS_CLEARABLE = ("summary", "details", "links", "drafts", "questions", "svg_file")


def die(msg):
    sys.exit(f"item.py: {msg}")


def check_id(kind, v):
    if not leaderlib.ID_RE.match(v or ""):
        die(f"{kind} は英数字・-・_ で（64 字まで）: {v!r}")


class ProjectFile:
    def __init__(self, work, pid):
        check_id("プロジェクトの id", pid)
        self.work, self.pid = work, pid
        self.path = work / "items" / f"{pid}.json"
        self.rel = f"items/{pid}.json"

    def load(self, create):
        if not self.path.exists():
            if not create:
                die(f"{self.rel} が無い（project か put で作る）")
            return {"project": {"id": self.pid, "label": self.pid}, "items": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as e:
            die(f"{self.rel} が壊れている（上書きしない。直してからやり直す）: {e}")
        if not isinstance(data, dict):
            die(f"{self.rel} の一番外がオブジェクトでない")
        if not isinstance(data.get("project"), dict):
            data["project"] = {"id": self.pid, "label": self.pid}
        data["project"]["id"] = self.pid
        if not isinstance(data.get("items"), list):
            data["items"] = []
        return data

    def lock(self):
        return leaderlib.locked(self.work / "items" / f".{self.pid}.lock")

    def save(self, data):
        leaderlib.write_json(self.path, data)


def find_item(data, iid, create=False):
    for it in data["items"]:
        if isinstance(it, dict) and str(it.get("id")) == iid:
            return it
    if not create:
        die(f"項目 {iid} が無い（put で作る）")
    it = {"id": iid, "title": iid, "status": "running"}
    data["items"].append(it)
    return it


def drop_status_override(work, iids):
    """leader.json に残る状態の上書きを消す（item.py で決めた状態を正にする）。"""
    path = work / "leader.json"
    if not path.exists():
        return []
    with leaderlib.leader_lock(work):
        data, err = leaderlib.read_json(path, None)
        if err or not isinstance(data, dict) or not isinstance(data.get("status"), dict):
            return []
        gone = [i for i in iids if i in data["status"]]
        if gone:
            for i in gone:
                del data["status"][i]
            leaderlib.write_json(path, data)
        return gone


def qid_owner_elsewhere(work, qid, pid, iid):
    """ほかの項目（ほかのファイルを含む）が同じ問いの id を使っていれば、その項目の id。"""
    data = leaderlib.load_items(work)[0]
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        if str(it.get("id")) == iid and it.get("project") == pid:
            continue
        for q in leaderlib.as_list(it.get("questions")):
            if isinstance(q, dict) and str(q.get("id")) == qid:
                return str(it.get("id"))
    return None


def rel_to_root(file, root):
    p = pathlib.Path(file).expanduser().resolve()
    r = pathlib.Path(root).expanduser().resolve()
    try:
        return p, p.relative_to(r).as_posix()
    except ValueError:
        die(f"下書きのファイルが --root（{r}）の外にある: {p}")


def summarize(it):
    qs = [q for q in leaderlib.as_list(it.get("questions")) if isinstance(q, dict)]
    ds = [d for d in leaderlib.as_list(it.get("drafts")) if isinstance(d, dict)]
    return f"状態 {it.get('status', 'decide')}・問い {len(qs)}・下書き {len(ds)}"


# ---------- コマンド ----------

def cmd_show(a, work):
    pf = ProjectFile(work, a.project)
    data = pf.load(create=False)
    leader = leaderlib.load_leader(work)
    replies = leader.get("replies") if isinstance(leader.get("replies"), dict) else {}
    answers = {}
    decisions = leaderlib.read_decisions(work)
    done_map = leaderlib.user_done_map(decisions)
    for n, d in decisions:
        if d.get("qid") not in leaderlib.RESERVED_QIDS:
            answers[str(d.get("qid"))] = (n, d)
    proj = data["project"]
    lead = leaderlib.clean_lead(proj.get("lead"))
    head = f"{pf.rel}  {proj.get('label') or pf.pid}"
    if lead:
        head += f"  担当: {lead['status']}「{lead['summary']}」（{lead['updated']}）"
    print(head)
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        st = leaderlib.effective_status(it, leader, done_map)
        line = f"- {it.get('id')} [{st}{'・急ぎ' if it.get('urgent') else ''}] {it.get('title', '')}"
        ud = done_map.get(str(it.get("id")))
        if ud:
            line += f"  （利用者が{'完了にした' if ud.get('done') is True else '未完了に戻した'} #{ud['line']}）"
        print(line)
        for q in leaderlib.as_list(it.get("questions")):
            if not isinstance(q, dict):
                continue
            n_ans = answers.get(str(q.get("id")))
            if n_ans:
                n, ans = n_ans
                note = (ans.get("note") or "").replace("\n", " ")
                note = note[:60] + ("…" if len(note) > 60 else "")
                mark = f"答え #{n}「{ans.get('choice', '')}」" + (f" メモ: {note}" if note else "")
            else:
                mark = "答え待ち"
            reply = "・返事あり" if replies.get(str(q.get("id"))) else ""
            print(f"    問い {q.get('id')}: {mark}{reply}")
        ds = [str(d.get("label", "下書き")) for d in leaderlib.as_list(it.get("drafts")) if isinstance(d, dict)]
        if ds:
            print(f"    下書き: {'・'.join(ds)}")


def cmd_project(a, work):
    pf = ProjectFile(work, a.project)
    with pf.lock():
        data = pf.load(create=True)
        if a.label:
            data["project"]["label"] = a.label
        pf.save(data)
    print(f"書いた: {pf.rel}（{data['project'].get('label')}）")


def cmd_lead(a, work):
    pf = ProjectFile(work, a.project)
    if a.status not in leaderlib.LEAD_STATUSES:
        die(f"担当の状態は {' / '.join(leaderlib.LEAD_STATUSES)} のどれか: {a.status}")
    summary = " ".join(a.summary.split())
    if len(summary) > 80:
        print("注意: 1行の要約は 80 字ほどまでにする（画面で切れる）", file=sys.stderr)
    with pf.lock():
        data = pf.load(create=True)
        data["project"]["lead"] = {"status": a.status, "summary": summary[:200], "updated": leaderlib.now_short()}
        pf.save(data)
    print(f"書いた: {pf.rel} の担当 {a.status}「{summary[:40]}」")


def cmd_put(a, work):
    check_id("項目の id", a.item)
    pf = ProjectFile(work, a.project)
    extra = None
    if a.json:
        try:
            raw = sys.stdin.read() if a.json == "-" else pathlib.Path(a.json).read_text(encoding="utf-8")
            extra = json.loads(raw)
        except (OSError, ValueError) as e:
            die(f"--json が読めない: {e}")
        if not isinstance(extra, dict):
            die("--json はオブジェクト（{...}）にする")
    if a.status and a.status not in leaderlib.STATUSES:
        die(f"状態は {' / '.join(leaderlib.STATUSES)} のどれか: {a.status}")
    for f in a.clear:
        if f not in FIELDS_CLEARABLE:
            die(f"--clear に使える欄: {' / '.join(FIELDS_CLEARABLE)}")
    if a.summary and len(a.summary) > 3:
        print("注意: summary は3行までにする（残りは --details へ）", file=sys.stderr)
    with pf.lock():
        data = pf.load(create=True)
        it = find_item(data, a.item, create=True)
        if extra:
            for k, v in extra.items():
                if k not in ("id", "project"):
                    it[k] = v
        for f in a.clear:
            it.pop(f, None)
        for key in ("title", "group", "status"):
            v = getattr(a, key)
            if v is not None:
                it[key] = v
        if a.urgent is not None:
            it["urgent"] = a.urgent
        if a.summary is not None:
            it["summary"] = a.summary
        if a.details is not None:
            it["details"] = a.details
        if a.link:
            it["links"] = [{"label": l, "url": u} for l, u in a.link]
        if a.svg_file:
            it["svg_file"] = a.svg_file
        it.pop("project", None)
        pf.save(data)
    gone = drop_status_override(work, [a.item]) if a.status else []
    print(f"書いた: {pf.rel} / {a.item}（{summarize(it)}）" + ("・leader.json の状態を消した" if gone else ""))


def cmd_status(a, work):
    pf = ProjectFile(work, a.project)
    if a.status not in leaderlib.STATUSES:
        die(f"状態は {' / '.join(leaderlib.STATUSES)} のどれか: {a.status}")
    with pf.lock():
        data = pf.load(create=False)
        it = find_item(data, a.item)
        it["status"] = a.status
        pf.save(data)
    gone = drop_status_override(work, [a.item])
    print(f"書いた: {pf.rel} / {a.item} の状態 {a.status}" + ("・leader.json の状態を消した" if gone else ""))


def cmd_ask(a, work):
    check_id("問いの id", a.qid)
    if a.qid in leaderlib.RESERVED_QIDS:
        die(f"問いの id に {a.qid} は使えない")
    pf = ProjectFile(work, a.project)
    if not a.remove:
        if not a.text:
            die("問いの文が要る（消すなら --remove）")
        other = qid_owner_elsewhere(work, a.qid, a.project, a.item)
        if other:
            die(f"問いの id {a.qid} は項目 {other} が使っている（全項目を通して重ならないようにする）")
    to_decide = False
    with pf.lock():
        data = pf.load(create=False)
        it = find_item(data, a.item)
        qs = [q for q in leaderlib.as_list(it.get("questions")) if isinstance(q, dict)]
        rest = [q for q in qs if str(q.get("id")) != a.qid]
        if a.remove:
            if len(rest) == len(qs):
                die(f"項目 {a.item} に問い {a.qid} が無い")
            it["questions"] = rest
            what = "消した"
        else:
            new = {"id": a.qid, "text": a.text, "options": a.option}
            replaced = len(rest) != len(qs)
            # 置き換えなら同じ位置に、新しければ後ろに
            out, put = [], False
            for q in qs:
                if str(q.get("id")) == a.qid and not put:
                    out.append(new)
                    put = True
                elif str(q.get("id")) != a.qid:
                    out.append(q)
            if not put:
                out.append(new)
            it["questions"] = out
            what = "置き換えた" if replaced else "足した"
            if not a.keep_status:
                to_decide = True
                if it.get("status") != "decide":
                    it["status"] = "decide"
                    what += "・状態を decide にした"
        pf.save(data)
    gone = drop_status_override(work, [a.item]) if to_decide else []
    if not a.remove and not a.option:
        print("注意: 選択肢が無い（利用者はメモだけで答えることになる）", file=sys.stderr)
    print(f"書いた: {pf.rel} / {a.item} の問い {a.qid} を{what}" + ("・leader.json の状態を消した" if gone else ""))


def cmd_draft(a, work):
    pf = ProjectFile(work, a.project)
    entry = None
    if not a.remove:
        if bool(a.file) == bool(a.text is not None):
            die("--file か --text のどちらか1つを付ける")
        entry = {"label": a.label}
        if a.file:
            path, rel = rel_to_root(a.file, a.root)
            src = {"file": rel}
            if a.heading:
                src["heading"] = a.heading
            if a.nth and a.nth > 1:
                src["nth"] = a.nth
            if a.whole:
                src["whole"] = True
            if a.between_rules:
                src["between_rules"] = True
            entry["src"] = src
            # 画面と同じ抜き方で、抜けるかを確かめる
            import server  # noqa: E402  (server.py の section を使う。サーバーは起動しない)
            text, failed = server.draft_text(src, pathlib.Path(a.root).expanduser().resolve())
            if failed:
                print(f"注意: {text}", file=sys.stderr)
            else:
                print(f"抜き出し: {len(text)} 字（{text.splitlines()[0][:30] if text else '空'}…）")
        else:
            entry["text"] = a.text
        if a.where:
            entry["where"] = a.where
        if a.link:
            entry["link"] = a.link
    with pf.lock():
        data = pf.load(create=False)
        it = find_item(data, a.item)
        ds = [d for d in leaderlib.as_list(it.get("drafts")) if isinstance(d, dict)]
        rest = [d for d in ds if str(d.get("label")) != a.label]
        if a.remove:
            if len(rest) == len(ds):
                die(f"項目 {a.item} に下書き「{a.label}」が無い")
            it["drafts"] = rest
            what = "消した"
        else:
            out, put = [], False
            for d in ds:
                if str(d.get("label")) == a.label:
                    if not put:
                        out.append(entry)
                        put = True
                else:
                    out.append(d)
            if not put:
                out.append(entry)
            it["drafts"] = out
            what = "置き換えた" if len(rest) != len(ds) else "足した"
        pf.save(data)
    print(f"書いた: {pf.rel} / {a.item} の下書き「{a.label}」を{what}")


def main():
    ap = argparse.ArgumentParser(description="items/<project>.json の項目を書き換える",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("dir", help="作業フォルダー")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="項目と問いと答えを短く出す")
    s.add_argument("project")

    s = sub.add_parser("project", help="ファイルを作る・タブの名前を変える")
    s.add_argument("project")
    s.add_argument("--label")

    s = sub.add_parser("lead", help="担当の状態と1行の要約")
    s.add_argument("project")
    s.add_argument("status", help="working / idle / waiting")
    s.add_argument("summary", help="1行の要約（80 字ほどまで）")

    s = sub.add_parser("put", help="項目を足す・直す")
    s.add_argument("project")
    s.add_argument("item")
    s.add_argument("--title")
    s.add_argument("--group")
    s.add_argument("--status", help="decide / running / done")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--urgent", dest="urgent", action="store_true", default=None)
    g.add_argument("--not-urgent", dest="urgent", action="store_false")
    s.add_argument("--summary", action="append", help="1行ずつ（付けると置き換える）")
    s.add_argument("--details", action="append", help="1行ずつ（付けると置き換える）")
    s.add_argument("--link", nargs=2, action="append", metavar=("LABEL", "URL"))
    s.add_argument("--svg-file", help="作業フォルダーからの相対パス")
    s.add_argument("--clear", action="append", default=[], help="空にする欄")
    s.add_argument("--json", help="重ねる欄を JSON で（ファイル、- で標準入力）")

    s = sub.add_parser("status", help="状態を変える")
    s.add_argument("project")
    s.add_argument("item")
    s.add_argument("status", help="decide / running / done")

    s = sub.add_parser("ask", help="問いを足す・置き換える・消す")
    s.add_argument("project")
    s.add_argument("item")
    s.add_argument("qid")
    s.add_argument("text", nargs="?")
    s.add_argument("--option", action="append", default=[], help="選択肢（推奨を先頭に）")
    s.add_argument("--keep-status", action="store_true", help="状態を decide に変えない")
    s.add_argument("--remove", action="store_true")

    s = sub.add_parser("draft", help="下書きを足す・置き換える・消す（label で見分ける）")
    s.add_argument("project")
    s.add_argument("item")
    s.add_argument("--label", required=True)
    s.add_argument("--file", help="下書きのファイル（--root の中）")
    s.add_argument("--heading", help="見出しの行の前方一致（例: '## 本文'）")
    s.add_argument("--nth", type=int, default=1)
    s.add_argument("--whole", action="store_true", help="節の全体を出す")
    s.add_argument("--between-rules", action="store_true", help="最初の2本の --- の間だけを出す")
    s.add_argument("--text", help="短い文面を直接書く")
    s.add_argument("--where", help="貼る場所")
    s.add_argument("--link", help="「開く」のリンク")
    s.add_argument("--root", default=".", help="サーバーの --root と同じ所（既定はカレントディレクトリ）")
    s.add_argument("--remove", action="store_true")

    a = ap.parse_args()
    work = pathlib.Path(a.dir).expanduser()
    if not work.is_dir():
        die(f"作業フォルダーが無い: {work}")
    {"show": cmd_show, "project": cmd_project, "lead": cmd_lead, "put": cmd_put, "status": cmd_status,
     "ask": cmd_ask, "draft": cmd_draft}[a.cmd](a, work)


if __name__ == "__main__":
    main()
