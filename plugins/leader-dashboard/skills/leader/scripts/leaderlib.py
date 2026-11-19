"""leader-dashboard の道具が共有する部品（標準ライブラリだけ。Python 3.9 以上）。

- 作業フォルダーの見つけ方
- items.json と items/<project>.json を合わせて読む
- JSON を一時ファイル経由で置き換える・排他のロック
- decisions.jsonl と cursor（リーダーが振り分け済みの行数）の読み方

フックからも読み込むので、重いモジュールは import しない。
"""
import contextlib
import datetime
import json
import os
import pathlib
import re

try:
    import fcntl
except ImportError:  # Windows では排他をあきらめる
    fcntl = None

STATUSES = ("decide", "running", "done")
LEAD_STATUSES = ("working", "idle", "waiting")
DONE_QID = "__done__"  # 画面の完了ボタン（POST /api/done）の行
RESERVED_QIDS = ("message", DONE_QID)  # 問いの id に使えない
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ENV_DIR = "LEADER_DASHBOARD_DIR"


def now():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def now_short():
    return datetime.datetime.now().astimezone().strftime("%m/%d %H:%M")


def today():
    return datetime.date.today().isoformat()


# ---------- 作業フォルダー ----------

def find_workdir(cwd=None, project_dir=None):
    """作業フォルダーを決まりの順に探す。見つからなければ None。

    1. 環境変数 LEADER_DASHBOARD_DIR（相対なら cwd から）
    2. <cwd>/.leader/<今日の日付>
    3. <project_dir>/.leader/<今日の日付>
    """
    base = pathlib.Path(cwd or os.getcwd())
    env = os.environ.get(ENV_DIR)
    if env:
        p = pathlib.Path(env).expanduser()
        p = p if p.is_absolute() else base / p
        return p if p.is_dir() else None
    for root in (base, pathlib.Path(project_dir) if project_dir else None):
        if root is None:
            continue
        p = root / ".leader" / today()
        if p.is_dir():
            return p
    return None


def default_workdir():
    """server.py の --dir の既定（無くても返す）。"""
    env = os.environ.get(ENV_DIR)
    return pathlib.Path(env).expanduser() if env else pathlib.Path(".leader") / today()


# ---------- JSON の読み書き ----------

def read_json(path, default):
    path = pathlib.Path(path)
    if not path.exists():
        return default, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (ValueError, OSError) as e:
        return default, f"{path.name} が読めない: {e}"


def write_json(path, obj):
    """一時ファイルに書いてから置き換える（読む側が書きかけを見ない）。"""
    import tempfile  # フックを速くするため、書くときだけ読む

    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_text(path, text):
    import tempfile

    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(lockfile):
    lockfile = pathlib.Path(lockfile)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    f = open(lockfile, "w")
    try:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        f.close()


def leader_lock(work):
    """leader.json・cursor・roster.json を書くときの排他（reply.py と同じ鍵）。"""
    return locked(pathlib.Path(work) / ".leader.lock")


def as_list(v):
    return v if isinstance(v, list) else []


# ---------- 項目を合わせて読む ----------

def project_files(work):
    d = pathlib.Path(work) / "items"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if not p.name.startswith("."))


def clean_lead(lead):
    """担当の状態 {"status","summary","updated"} を整える。形が違えば None。"""
    if not isinstance(lead, dict):
        return None
    st = lead.get("status")
    if st not in LEAD_STATUSES:
        return None
    return {"status": st, "summary": str(lead.get("summary") or "")[:200],
            "updated": str(lead.get("updated") or "")[:20]}


def load_items(work):
    """items.json と items/<project>.json を合わせ、items.json と同じ形の dict にして返す。

    返り値: (data, errors, warnings, files)
      data は {"title","headline","schedule","projects","items"}。
      items/<project>.json から来た項目とプロジェクトには source_file が付く。
    """
    work = pathlib.Path(work)
    errors, warnings, files = [], [], []
    top, err = read_json(work / "items.json", {})
    if err:
        errors.append(err)
    if not isinstance(top, dict):
        errors.append("items.json の一番外はオブジェクト（{...}）にする")
        top = {}
    if (work / "items.json").exists():
        files.append("items.json")
    projects = [dict(p) for p in as_list(top.get("projects")) if isinstance(p, dict) and p.get("id")]
    by_id = {str(p["id"]): p for p in projects}
    # 形の点検は呼ぶ側で（オブジェクトでない要素もそのまま渡す）
    items = [dict(it) if isinstance(it, dict) else it for it in as_list(top.get("items"))]

    for path in project_files(work):
        rel = f"items/{path.name}"
        data, err = read_json(path, None)
        if err:
            errors.append(f"{rel} が読めない: {err.split(': ', 1)[-1]}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{rel} の一番外はオブジェクト（{{\"project\":..., \"items\": [...]}}）にする")
            continue
        files.append(rel)
        proj = data.get("project")
        if isinstance(proj, str):
            proj = {"id": proj}
        if not isinstance(proj, dict):
            proj = {}
        pid = str(proj.get("id") or path.stem)
        if pid != path.stem:
            warnings.append(f"{rel}: project.id（{pid}）がファイル名と違う")
        p = by_id.get(pid)
        if p is None:
            p = {"id": pid}
            projects.append(p)
            by_id[pid] = p
        if proj.get("label"):
            p["label"] = str(proj["label"])
        lead = clean_lead(proj.get("lead"))
        if lead:
            p["lead"] = lead
        elif proj.get("lead") is not None:
            warnings.append(f"{rel}: lead の形が違う（status は {' / '.join(LEAD_STATUSES)}）")
        p["source_file"] = rel
        for n, it in enumerate(as_list(data.get("items")), 1):
            if not isinstance(it, dict):
                warnings.append(f"{rel}: items の {n} 番目がオブジェクトでない")
                continue
            it = dict(it)
            if it.get("project") not in (None, "", pid):
                warnings.append(f"{rel}: 項目 {it.get('id')} の project（{it.get('project')}）を {pid} として読んだ")
            it["project"] = pid
            it["source_file"] = rel
            items.append(it)

    out = {
        "title": top.get("title"),
        "headline": top.get("headline"),
        "schedule": top.get("schedule"),
        "projects": projects,
        "items": items,
    }
    return out, errors, warnings, files


def load_leader(work):
    data, _ = read_json(pathlib.Path(work) / "leader.json", {})
    return data if isinstance(data, dict) else {}


def effective_status(item, leader, done_map=None):
    """画面に出す状態。leader.json の状態が項目の状態に勝ち、利用者の完了ボタンの最後の操作がさらに勝つ。

    done_map: user_done_map() の返り値。done: true なら done、false なら済みのときだけ decide に戻す。
    """
    over = leader.get("status") if isinstance(leader.get("status"), dict) else {}
    st = over.get(str(item.get("id")), item.get("status", "decide"))
    st = st if st in STATUSES else "decide"
    ud = (done_map or {}).get(str(item.get("id")))
    if ud:
        if ud.get("done") is True:
            st = "done"
        elif st == "done":
            st = "decide"
    return st


def user_done_map(decisions):
    """[(行番号, dict)] から、項目ごとの完了ボタンの最後の操作を {項目の id: dict（line 付き）} で返す。"""
    out = {}
    for n, d in decisions:
        if d.get("qid") == DONE_QID and d.get("item"):
            out[str(d["item"])] = dict(d, line=n)
    return out


def item_index(data):
    """項目の id → 項目、問いの id → 項目の id。"""
    items, qowner = {}, {}
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id"))
        items.setdefault(iid, it)
        for q in as_list(it.get("questions")):
            if isinstance(q, dict) and q.get("id"):
                qowner.setdefault(str(q["id"]), iid)
    return items, qowner


# ---------- 答えと cursor ----------

def count_lines(path):
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def read_cursor(work):
    try:
        return max(0, int((pathlib.Path(work) / "cursor").read_text().strip() or 0))
    except (OSError, ValueError):
        return 0


def read_decisions(work, start=1):
    """decisions.jsonl の start 行目（1 から）以降を [(行番号, dict)] で返す。"""
    out = []
    path = pathlib.Path(work) / "decisions.jsonl"
    if not path.exists():
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            if n < start:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append((n, d))
    return out


def pid_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def server_info(work):
    """server.json（server.py が起動時に書く）を読み、動いているかを添えて返す。無ければ None。"""
    data, _ = read_json(pathlib.Path(work) / "server.json", None)
    if not isinstance(data, dict):
        return None
    data["alive"] = pid_alive(data.get("pid"))
    return data
