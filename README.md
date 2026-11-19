# claude-leader-dashboard

「今日のリーダー」を Claude Code に任せるプラグイン。

Claude がその日のタスクを洗い出し、タスクごとに裏のエージェントを出して下書きまで作る。判断が要ることだけを、手元のブラウザーで開くダッシュボードに並べる。利用者はタブを開き、1件ずつ選択肢とメモで答える。答えは Claude に届き、Claude は動いた結果を同じ画面に返す。

- チャットには URL と件数だけが出る。判断の材料・下書き・図はダッシュボードで読む
- 画面はプロジェクトごとのタブで、タブの中は左に一覧、右に1件。ページ全体はスクロールしない
- `j` / `k`（または `↑` / `↓`）で項目、`←` / `→` でタブを移る。1件の問いに全部答えると、次の判断待ちへ進む
- 済んだ項目は一覧の下の「済み（N）」に畳まれる
- Claude が項目を変えると「更新」の印が付く。開くと光って既読になる。Claude の最新の動きはヘッダーの帯に出て、新しく来るとトーストで知らせる
- ヘッダーの欄から、どのタブからでも Claude に一言送れる
- 下書きは元のファイルから毎回読む。コピーのボタンと「開く」のリンクが付く
- 図は Claude が書いた SVG をそのまま描く
- 依存は Python 3.9 以上の標準ライブラリと bash だけ。サーバーは 127.0.0.1 だけで受ける

## 入れ方

Claude Code で次を実行する。

```
/plugin marketplace add kumachan99/claude-leader-dashboard
/plugin install leader-dashboard@claude-leader-dashboard
```

手元に clone したものから入れるときは、1行目を `/plugin marketplace add /path/to/claude-leader-dashboard` にする。

## 使い方

Claude Code に、たとえば次のように頼む。

> 今日のリーダーはあなた。タスクごとにエージェントを出して下書きまで作って。判断はダッシュボードで受けて。

スキル `leader` が読み込まれる（`/leader-dashboard:leader` でも呼べる）。

1. Claude がタスクを洗い出し、エージェントを出す
2. Claude がチャットに書いた URL（既定は `http://127.0.0.1:8765/`）を開く
3. タブを選び、左の一覧から1件ずつ開いて答える。項目に無いことは、ヘッダーの「Claude に一言」で頼める
4. Claude の返事は、問いの下と、ヘッダーの帯と、全体のタブの「Claude からの返事・動いたこと」に出る。画面は4秒ごとに新しくなる

送信・投稿・予約・購入・削除のように外に出る操作は、利用者がその項目に答えてから行う決まりにしてある（スキルの「守ること」）。

## Claude なしで画面を試す

例の `items.json`（架空の私生活の例）で起動できる。

```
cd plugins/leader-dashboard/skills/leader/examples
cp -r demo /tmp/leader-demo
python3 -I ../scripts/server.py --dir /tmp/leader-demo --root . --port 8799
```

`http://127.0.0.1:8799/` を開く。別の端末で次を動かすと、Claude 側の動きを手で試せる。

```
cd plugins/leader-dashboard/skills/leader/examples
bash ../scripts/wait_decision.sh /tmp/leader-demo 0    # 画面で答えを送ると、20 秒後に終わる
python3 -I ../scripts/reply.py /tmp/leader-demo --qid trip-inn-pick --text "宿 A で進める"
python3 -I ../scripts/reply.py /tmp/leader-demo --status trip-inn done --log "宿 A に決めた"
```

## 作業フォルダー

サーバーは `--dir` で渡したフォルダーを読み書きする（既定は `.leader/<今日の日付>`）。

| ファイル | 書くのは | 中身 |
| --- | --- | --- |
| `items.json` | Claude | 項目の定義。サーバーは要求のたびに読み直す |
| `leader.json` | `reply.py` | Claude の返事・項目の状態・動いたこと |
| `decisions.jsonl` | サーバー | 利用者の答え。1行に1件 |
| `drafts/` | エージェント | 下書き |

下書きや判断に私的な情報が入るので、作業フォルダーは git に入れない。リポジトリの中で使うなら、`.gitignore` か `.git/info/exclude` に `.leader/` を足す。

`items.json` の書き方は [SKILL.md](plugins/leader-dashboard/skills/leader/SKILL.md) の「items.json の書き方」にある。例は [examples/demo/items.json](plugins/leader-dashboard/skills/leader/examples/demo/items.json)。

## 中身

```
.claude-plugin/marketplace.json        マーケットプレイスの定義
plugins/leader-dashboard/
  .claude-plugin/plugin.json           プラグインの定義
  skills/leader/
    SKILL.md                           Claude 向けの手順
    scripts/server.py                  ダッシュボードのサーバー
    scripts/index.html                 画面
    scripts/wait_decision.sh           答えが届くまで待つ
    scripts/reply.py                   leader.json に返事・状態・動いたことを書く
    examples/demo/items.json           例の項目
    examples/notes/                    例の下書きの元になるファイル
```

## 安全のこと

- サーバーは 127.0.0.1 だけで受ける。`Host` と `Origin` を確かめ、よそのページからの書き込みと DNS リバインディングを断る
- 下書きとして読むファイルは `--root` の中だけ、SVG のファイルは作業フォルダーの中だけ
- SVG の `<script>`・`on...` の属性・`javascript:` のリンクは描く前に落とす

## ライセンス

MIT License。

Copyright (c) 2026 Takahito Okuma
