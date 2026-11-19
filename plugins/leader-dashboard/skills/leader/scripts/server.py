#!/usr/bin/env python3
"""リーダーのダッシュボード（ローカル専用の HTTP サーバー。127.0.0.1 だけで受ける）。

使い方:
  python3 -I server.py --dir .leader/2026-11-19 --port 8765 --root .
  python3 -I server.py --dir .leader/2026-11-19 --root . --check   # items.json の点検だけ

作業フォルダー（--dir）に置くもの:
  items.json         項目の定義（Claude が書く。要求のたびに読み直す）
  items/<id>.json    プロジェクトごとの項目（担当のエージェントが item.py で書く）。items.json と合わせて返す
  leader.json        Claude の返事・状態・動いたこと（reply.py で書く）
  decisions.jsonl    利用者の答え（このサーバーが1行ずつ足す）
  cursor             リーダーが振り分け済みの行数（route.py が書く）
  server.json        起動中のサーバーの URL・pid（このサーバーが書き、止まるときに消す）
無いファイルは空として扱う。--dir を省くと、環境変数 LEADER_DASHBOARD_DIR か .leader/<今日の日付>。

GET  /               index.html
GET  /api/state      items.json＋下書きの本文＋答え＋返事
POST /api/decision   {"qid","choice","note"} を decisions.jsonl に1行足す
POST /api/message    {"text", "item"} を decisions.jsonl に1行足す（qid は "message"）
POST /api/done       {"item", "done": true|false} を decisions.jsonl に1行足す（qid は "__done__"）。
                     state の各項目に最後の操作を user_done で付け、status は最後の操作が勝つ

下書きの src は --root からの相対パスで、毎回ファイルから読む。--root の外は読まない。
svg_file は作業フォルダーからの相対パスで、作業フォルダーの外は読まない。
依存は Python の標準ライブラリだけ（3.9 以上）。
"""
import argparse
import atexit
import datetime
import hashlib
import http.server
import json
import os
import pathlib
import re
import signal
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # python3 -I でも同じフォルダーの leaderlib を読む
import leaderlib  # noqa: E402
HOST = "127.0.0.1"
STATUSES = ("decide", "running", "done")
MAX_FILE = 2_000_000  # 下書き・SVG として読むファイルの上限（バイト）
MAX_BODY = 100_000  # POST の本文の上限（バイト）


def now():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def inside(rel, base):
    """base からの相対パス rel を解決し、base の中に収まるときだけ返す。"""
    if not isinstance(rel, str) or not rel:
        return None
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        return None
    return p


def read_limited(path):
    if path.stat().st_size > MAX_FILE:
        raise OSError(f"大きすぎる（{MAX_FILE} バイトまで）")
    return path.read_text(encoding="utf-8", errors="replace")


# ---------- 下書きの本文を抜く ----------

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#+)\s")


def section(text, heading=None, nth=1, whole=False, between_rules=False):
    """text の中の見出し heading（前方一致）の nth 番目の節の本文を返す。

    - heading を省くとファイル全体を節とみなす
    - between_rules: 節の中の最初の2本の `---` に挟まれた所だけを、そのまま返す
    - whole: 節の本文をそのまま返す
    - どちらでもなければ、引用（>）の行だけを抜く。引用が無ければ最初のコードブロックの中身、
      それも無ければ節の本文をそのまま返す
    返り値は (本文, 失敗したか)。
    """
    lines = text.splitlines()
    if heading:
        level = len(heading) - len(heading.lstrip("#"))
        count, out, in_fence = 0, None, False
        for line in lines:
            if FENCE.match(line):
                in_fence = not in_fence
                if out is not None:
                    out.append(line)
                continue
            if out is None:
                if not in_fence and line.startswith(heading):
                    count += 1
                    if count == nth:
                        out = []
                continue
            m = HEADING.match(line)
            if not in_fence and m and level and len(m.group(1)) <= level:
                break
            out.append(line)
        if out is None:
            return f"（見出し「{heading}」の {nth} 番目が見つからない）", True
    else:
        out = lines
    if between_rules:
        idx = [i for i, l in enumerate(out) if l.strip() == "---"]
        if len(idx) >= 2:
            out = out[idx[0] + 1 : idx[1]]
        return "\n".join(out).strip(), False
    body = re.sub(r"\n-{3,}\s*$", "", "\n".join(out).strip()).strip()
    if whole:
        return body, False
    # 注記と文面が混ざる節は、引用（>）かコードブロックだけを文面として抜く
    quoted, gap = [], False
    for l in out:
        if l.lstrip().startswith(">"):
            if gap and quoted:
                quoted.append("")
            quoted.append(re.sub(r"^\s*> ?", "", l))
            gap = False
        elif quoted:
            gap = True
    if quoted:
        return "\n".join(quoted).strip(), False
    m = re.search(r"^\s*(```|~~~)[^\n]*\n(.*?)^\s*\1", body, re.S | re.M)
    if m:
        return m.group(2).rstrip(), False
    return body, False


def draft_text(src, root):
    if isinstance(src, str):
        src = {"file": src}
    if not isinstance(src, dict):
        return "（src の形が違う。{\"file\": ..., \"heading\": ...} で書く）", True
    path = inside(src.get("file"), root)
    if path is None:
        return f"（--root の外か、パスが空: {src.get('file')!r}）", True
    try:
        text = read_limited(path)
    except OSError as e:
        return f"（読めなかった: {src.get('file')}: {e}）", True
    try:
        nth = max(1, int(src.get("nth", 1)))
    except (TypeError, ValueError):
        nth = 1
    return section(text, src.get("heading"), nth, bool(src.get("whole")), bool(src.get("between_rules")))


# ---------- SVG とリンクの手当て ----------

def clean_svg(s):
    """Claude が書いた SVG から、スクリプトとして動く所だけを落とす。"""
    if not isinstance(s, str) or "<svg" not in s:
        return ""
    s = re.sub(r"(?is)<script\b.*?</script\s*>", "", s)
    s = re.sub(r"(?is)<foreignObject\b.*?</foreignObject\s*>", "", s)
    s = re.sub(r"""(?i)\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", "", s)
    s = re.sub(r"""(?i)((?:xlink:)?href\s*=\s*["']?)\s*(javascript|data|vbscript):""", r"\1#", s)
    return s


def safe_url(u):
    if not isinstance(u, str) or not u.strip():
        return ""
    return "" if re.match(r"(?i)\s*(javascript|data|vbscript):", u) else u.strip()


# ---------- 状態を組み立てる ----------

read_json = leaderlib.read_json


def load_decisions(work, cursor):
    """答えを読む。各行に行番号（line）と、リーダーが振り分け済みか（routed）を付ける。

    返り値: (問いの id → 最後の答え, 一言の一覧, 項目の id → 完了ボタンの最後の操作)
    """
    latest, messages, done = {}, [], {}
    for n, d in leaderlib.read_decisions(work):
        d = dict(d, line=n, routed=n <= cursor)
        if d.get("qid") == "message":
            messages.append(d)
        elif d.get("qid") == leaderlib.DONE_QID:
            if d.get("item"):
                done[str(d["item"])] = d  # 「完了」「未完了に戻す」。最後の操作が勝つ
        else:
            latest[d.get("qid")] = d
    return latest, messages, done


def as_list(v):
    return v if isinstance(v, list) else []


def build_state(cfg):
    data, errors, warnings, files = leaderlib.load_items(cfg.dir)
    leader, err = read_json(cfg.dir / "leader.json", {})
    if err:
        errors.append(err)
    if not isinstance(leader, dict):
        leader = {}
    replies = leader.get("replies") if isinstance(leader.get("replies"), dict) else {}
    cursor = leaderlib.read_cursor(cfg.dir)
    total = leaderlib.count_lines(cfg.dir / "decisions.jsonl")
    latest, messages, done = load_decisions(cfg.dir, cursor)

    projects = [dict(p) for p in as_list(data.get("projects")) if isinstance(p, dict) and p.get("id")]
    for p in projects:
        p["id"], p["label"] = str(p["id"]), str(p.get("label") or p["id"])
        lead = leaderlib.clean_lead(p.get("lead"))
        if lead:
            p["lead"] = lead
        else:
            p.pop("lead", None)
    known = {p["id"] for p in projects}

    out_items, seen_items, seen_q = [], set(), set()
    for n, raw in enumerate(as_list(data.get("items")), 1):
        if not isinstance(raw, dict):
            warnings.append(f"items.json: items の {n} 番目がオブジェクトでない")
            continue
        it = dict(raw)
        it["id"] = str(it.get("id") or f"item-{n}")
        if it["id"] in seen_items:
            warnings.append(f"項目の id が重なっている: {it['id']}")
        seen_items.add(it["id"])
        it["project"] = str(it.get("project") or "other")
        if it["project"] not in known:
            projects.append({"id": it["project"], "label": "その他" if it["project"] == "other" else it["project"]})
            known.add(it["project"])
        it["title"] = str(it.get("title") or it["id"])
        it["group"] = str(it.get("group") or "")
        it["status"] = leaderlib.effective_status(it, leader, done)
        if it["id"] in done:
            it["user_done"] = done[it["id"]]
        it["summary"] = [str(x) for x in as_list(it.get("summary"))]
        it["details"] = [str(x) for x in as_list(it.get("details"))]
        it["links"] = [{"label": str(l.get("label") or l.get("url")), "url": safe_url(l.get("url"))}
                       for l in as_list(it.get("links")) if isinstance(l, dict) and safe_url(l.get("url"))]
        drafts = []
        for d in as_list(it.get("drafts")):
            if not isinstance(d, dict):
                continue
            d = dict(d)
            src = d.pop("src", None)
            if src is not None:
                d["text"], d["error"] = draft_text(src, cfg.root)
            d["text"] = str(d.get("text", ""))
            d["label"] = str(d.get("label", "下書き"))
            d["link"] = safe_url(d.get("link"))
            drafts.append(d)
        it["drafts"] = drafts
        svg = it.pop("svg", None)
        svg_file = it.pop("svg_file", None)
        if svg_file:
            path = inside(svg_file, cfg.dir)
            if path is None:
                warnings.append(f"{it['id']}: svg_file は作業フォルダーの中に置く: {svg_file}")
            else:
                try:
                    svg = read_limited(path)
                except OSError as e:
                    warnings.append(f"{it['id']}: svg_file が読めない: {e}")
        it["svg"] = clean_svg(svg) if svg else ""
        qs = []
        for q in as_list(it.get("questions")):
            if not isinstance(q, dict) or not q.get("id"):
                warnings.append(f"{it['id']}: id の無い問いがある")
                continue
            q = dict(q)
            q["id"] = str(q["id"])
            if q["id"] in leaderlib.RESERVED_QIDS:
                warnings.append(f"{it['id']}: 問いの id に {q['id']} は使えない")
                continue
            if q["id"] in seen_q:
                warnings.append(f"問いの id が重なっている: {q['id']}")
            seen_q.add(q["id"])
            q["text"] = str(q.get("text", ""))
            q["options"] = [str(o) for o in as_list(q.get("options"))]
            q["answer"] = latest.get(q["id"])
            q["reply"] = replies.get(q["id"])
            qs.append(q)
        it["questions"] = qs
        out_items.append(it)

    return {
        "generated": now(),
        # 画面が既読を localStorage に覚えるときの鍵（作業フォルダーごとに分ける）
        "session": hashlib.sha1(str(cfg.dir).encode("utf-8")).hexdigest()[:12],
        "title": str(data.get("title") or "今日のリーダー"),
        "headline": str(leader.get("headline") or data.get("headline") or ""),
        "schedule": [s for s in as_list(data.get("schedule")) if isinstance(s, dict)],
        "projects": projects,
        "items": out_items,
        "messages": messages,
        "log": [l for l in as_list(leader.get("log")) if isinstance(l, dict)],
        # リーダーが route.py で振り分けた所（cursor）と、まだ振り分けていない答えの数
        "routing": {"total": total, "cursor": min(cursor, total), "pending": max(0, total - cursor)},
        "files": files,
        "errors": errors,
        "warnings": warnings,
    }


def find_item_of(cfg, qid):
    data = leaderlib.load_items(cfg.dir)[0]
    return leaderlib.item_index(data)[1].get(qid)


# ---------- HTTP ----------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "leader-dashboard"

    def log_message(self, fmt, *args):
        pass

    @property
    def cfg(self):
        return self.server.cfg

    def _host_ok(self):
        """DNS リバインディング対策: Host が 127.0.0.1 か localhost のときだけ受ける。"""
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self):
        """よそのページからの POST を断る（Origin があれば、このサーバー自身のときだけ通す）。"""
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        return origin.lower() in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            try:
                self._json(200, build_state(self.cfg))
            except Exception as e:  # 画面に出して直せるようにする
                self._json(500, {"error": repr(e)})
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._json(403, {"ok": False, "error": "forbidden"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"ok": False, "error": "Content-Type は application/json"})
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return self._json(413, {"ok": False})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"ok": False})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False})
        if self.path == "/api/decision":
            qid = str(body.get("qid", ""))[:80]
            if not qid or qid in leaderlib.RESERVED_QIDS:
                return self._json(400, {"ok": False, "error": "qid が要る"})
            rec = {"ts": now(), "item": find_item_of(self.cfg, qid), "qid": qid,
                   "choice": str(body.get("choice", ""))[:200], "note": str(body.get("note", ""))[:4000]}
        elif self.path == "/api/message":
            text = str(body.get("text", ""))[:4000]
            if not text.strip():
                return self._json(400, {"ok": False})
            rec = {"ts": now(), "qid": "message", "note": text}
            item = str(body.get("item") or "")[:80]
            if item:
                rec["item"] = item
        elif self.path == "/api/done":
            item = str(body.get("item") or "")[:80]
            done = body.get("done", True)
            if not item or not isinstance(done, bool):
                return self._json(400, {"ok": False, "error": "item と done（true / false）が要る"})
            if item not in leaderlib.item_index(leaderlib.load_items(self.cfg.dir)[0])[0]:
                return self._json(404, {"ok": False, "error": f"項目 {item} が無い"})
            rec = {"ts": now(), "qid": leaderlib.DONE_QID, "item": item, "done": done}
        else:
            return self._json(404, {"ok": False})
        with (self.cfg.dir / "decisions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._json(200, {"ok": True, "ts": rec["ts"]})


class Config:
    def __init__(self, dir, root):
        self.dir, self.root = dir, root


def write_server_info(work, url, port):
    """フックが URL と生死を知るための server.json を書き、止まるときに消す。"""
    path = work / "server.json"
    leaderlib.write_json(path, {
        "url": url, "port": port, "pid": os.getpid(), "started": leaderlib.now(),
        # 起動したセッション（Claude Code が Bash に渡す。無ければ空）。フックが別のセッションを見分ける
        "session": os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
    })

    def cleanup(*_):
        try:
            data, _ = read_json(path, {})
            if isinstance(data, dict) and data.get("pid") == os.getpid():
                path.unlink()
        except OSError:
            pass

    atexit.register(cleanup)

    def on_term(signum, frame):
        sys.exit(0)  # atexit を走らせる

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)


def main():
    ap = argparse.ArgumentParser(description="リーダーのダッシュボード（127.0.0.1 だけで受ける）")
    ap.add_argument("--dir", default=None, help="作業フォルダー（既定は環境変数 LEADER_DASHBOARD_DIR か .leader/<今日の日付>）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=".", help="下書きの src を読む起点（既定はカレントディレクトリ）")
    ap.add_argument("--check", action="store_true", help="items.json と items/*.json を点検して終わる")
    a = ap.parse_args()
    work = (pathlib.Path(a.dir).expanduser() if a.dir else leaderlib.default_workdir()).resolve()
    root = pathlib.Path(a.root).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"--root がフォルダーでない: {root}")
    cfg = Config(work, root)

    if a.check:
        if not work.is_dir():
            sys.exit(f"作業フォルダーが無い: {work}")
        st = build_state(cfg)
        bad = [f"{it['id']} / {d['label']}: {d['text']}" for it in st["items"] for d in it["drafts"] if d.get("error")]
        for k, msgs in (("エラー", st["errors"]), ("注意", st["warnings"]), ("下書き", bad)):
            for m in msgs:
                print(f"{k}: {m}")
        n_q = sum(len(it["questions"]) for it in st["items"])
        print(f"読んだ: {'・'.join(st['files']) or '（項目のファイルが無い）'}")
        print(f"項目 {len(st['items'])}・問い {n_q}・タブ {len(st['projects'])}")
        sys.exit(1 if st["errors"] or bad else 0)

    work.mkdir(parents=True, exist_ok=True)
    (work / "decisions.jsonl").touch(exist_ok=True)
    try:
        srv = http.server.ThreadingHTTPServer((HOST, a.port), Handler)
    except OSError as e:
        sys.exit(f"ポート {a.port} で起動できない（使用中かもしれない）: {e}")
    srv.cfg = cfg
    url = f"http://{HOST}:{a.port}/"
    write_server_info(work, url, a.port)
    print(f"ダッシュボード: {url}", flush=True)
    print(f"作業フォルダー: {work}", flush=True)
    print(f"下書きの起点:   {root}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
