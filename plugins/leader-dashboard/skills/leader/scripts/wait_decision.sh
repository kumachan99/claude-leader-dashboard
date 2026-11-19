#!/usr/bin/env bash
# 利用者の答えが届くまで待つ。
#
# 使い方:
#   wait_decision.sh <作業フォルダー> cursor     待つだけ。中身は出さない（route.py で読む）
#   wait_decision.sh <作業フォルダー> [基準の行数]  届いた行をそのまま出す（前からの使い方）
#
# cursor の型: decisions.jsonl の行数が cursor（route.py --ack が進める振り分け済みの行数。
# 無ければ 0）より増えるまで待つ。振り分けていない答えが残っていれば、すぐに終わる。
# 増えたら SETTLE 秒（既定 20）待って、続けて押された答えもまとめてから、
# 件数の1行と unread=<件数> を出して終わる。答えの中身は出さない（リーダーの会話を膨らませない）。
#
# 数字の型: 行数が基準（省くと今の行数）より増えるまで待ち、SETTLE 秒待ってから、
# 新しい行をそのまま出して終わる。最後の行は next_base=<行数>。次に待つときは、その数を基準に渡す。
#
# 環境変数: SETTLE（まとめて待つ秒、既定 20）、INTERVAL（見に行く間隔の秒、既定 2）
set -eu

dir=${1:?"使い方: wait_decision.sh <作業フォルダー> [cursor | 基準の行数]"}
f="$dir/decisions.jsonl"
settle=${SETTLE:-20}
interval=${INTERVAL:-2}

[ -d "$dir" ] || { echo "作業フォルダーが無い: $dir" >&2; exit 2; }
[ -e "$f" ] || : >"$f"

count() { wc -l <"$f" | tr -d ' '; }
cursor() {
  local c
  c=$(tr -dc '0-9' <"$dir/cursor" 2>/dev/null || true)
  echo "${c:-0}"
}

mode=number
if [ "${2:-}" = cursor ]; then
  mode=cursor
  base=$(cursor)
else
  base=${2:-$(count)}
fi
case $base in
  '' | *[!0-9]*) echo "基準の行数は数字で（または cursor）: $base" >&2; exit 2 ;;
esac

until [ "$(count)" -gt "$base" ]; do
  sleep "$interval"
  # cursor の型では、待っている間に route.py --ack で進んだ分を基準に入れ直す
  if [ "$mode" = cursor ]; then
    c=$(cursor)
    if [ "$c" -gt "$base" ]; then base=$c; fi
  fi
done
sleep "$settle"

n=$(count)
if [ "$mode" = cursor ]; then
  unread=$((n - $(cursor)))
  if [ "$unread" -lt 0 ]; then unread=0; fi
  echo "新しい答え ${unread} 件。読むのは route.py（route.py $dir --ack）"
  echo "unread=$unread"
else
  sed -n "$((base + 1)),${n}p" "$f"
  echo "next_base=$n"
fi
