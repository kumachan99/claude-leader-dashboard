# 画面（index.html）に向けたメモ

サーバーと道具の側で足したものと、画面で使えるもの。今ある欄の意味は変えていない（足しただけ）。

試すときの例は `plugins/leader-dashboard/skills/leader/examples/projects/` にある。`items.json` と、`items/blog.json`・`items/trip.json` の3ファイル。

```
cd plugins/leader-dashboard/skills/leader/examples
cp -r projects /tmp/leader-projects
python3 -I ../scripts/server.py --dir /tmp/leader-projects --root . --port 8799
```

## `/api/state` に足した欄

| 欄 | 形 | 画面ですること（案） |
| --- | --- | --- |
| `projects[].lead` | `{"status": "working" \| "idle" \| "waiting", "summary": "1行", "updated": "MM/DD HH:MM"}`。無いこともある | 7084194 で表示済み。サーバーは形の違う `lead` を落として渡す（`status` が3つのどれでもなければ欄ごと無い） |
| `projects[].source_file` | `"items/blog.json"`。`items.json` だけで決まるプロジェクトには無い | 出さなくてよい。点検の表示に使うなら |
| `items[].source_file` | 同上（項目がどのファイルから来たか） | 同上 |
| `items[].user_done` | `{"ts", "qid": "__done__", "item", "done": true \| false, "line", "routed"}`。完了ボタンを一度も押していなければ無い | 完了ボタンの今の向き（`done` が `true` なら「未完了に戻す」）。`status` はサーバーが最後の操作で決めて渡す |
| `questions[].answer.line`・`.routed` | 答えの `decisions.jsonl` の行番号と、リーダーが振り分け済みか | `routed: false` の答えに「Claude にまだ届いていない」印。`true` になったら「受け取り済み」 |
| `messages[].line`・`.routed` | 同上（一言） | 同上 |
| `routing` | `{"total": 行数, "cursor": 振り分け済みの行数, "pending": まだ振り分けていない件数}` | ヘッダーに「Claude が未処理 N」（`pending` が 0 なら出さない）。答えたのに Claude が動かないときの手がかりになる |
| `log[].project` | プロジェクトの id。担当が `reply.py --project` で書いた行にだけ付く | 帯と「動いたこと」に、プロジェクトの名前の小さな札を付ける |
| `files` | 読んだ項目のファイルの一覧（`["items.json", "items/blog.json", ...]`） | 出さなくてよい |

`warnings` と `errors` の文は、どのファイルの話かが分かるように `items/blog.json: ...` のように始まる。

## 完了ボタン（`POST /api/done`）

- 本文: `{"item": "<項目の id>", "done": true | false}`（`done` を省くと `true`）
- 返事: `200 {"ok": true, "ts": "..."}`。`item` が無い・`done` が真偽値でない → `400`。項目が無い → `404`。Host・Origin・Content-Type の点検は `/api/decision` と同じ（`403`・`415`）
- `decisions.jsonl` に `{"ts", "qid": "__done__", "item", "done"}` が1行入る
- 次の `/api/state` から、その項目の `status` は最後の操作で決まる。`done: true` なら `"done"`。`done: false` なら、済みのときだけ `"decide"` に戻り、それ以外はそのまま
- 問いの id に `__done__` と `message` は使えない（`/api/decision` は `400` を返す）

## 動きの順（画面から見えること）

1. 利用者が答える・一言を送る・完了を押す → `decisions.jsonl` に1行入り、`routing.pending` が増える
2. リーダーの `wait_decision.sh` が終わり、`route.py --ack` で読む → `routing.cursor` が進み、その行の `routed` が `true` になる
3. リーダーが担当に回す。担当が `item.py` で `items/<id>.json` を直す → 項目の「更新」の印、`lead` の状態と1行が変わる
4. 担当が `reply.py --project <id>` で返事とログを書く → 問いの下の返事と、帯の1行が変わる

## 気をつけること

- `status` は、サーバーが `leader.json` の上書きと完了ボタンの最後の操作を当てたあとの値。画面で計算し直さない
- 項目は `items.json` → `items/*.json`（ファイル名の順）で並ぶ。タブの並びは `items.json` の `projects` が先、残りはファイル名の順
- 再描画の判定（`generated` を除いて比べる）には、`routing` と `routed` の変化も入る。リーダーが振り分けるたびに1回描き直す
