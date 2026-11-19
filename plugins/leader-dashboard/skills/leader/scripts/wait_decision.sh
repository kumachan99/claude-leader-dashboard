#!/usr/bin/env bash
# 利用者の答えが届くまで待つ。
#
# 使い方: wait_decision.sh <作業フォルダー> [基準の行数]
#
# decisions.jsonl の行数が基準（省くと今の行数）より増えるまで待つ。
# 増えたら SETTLE 秒（既定 20）待ち、続けて押された答えもまとめてから、
# 新しい行をそのまま出して終わる。最後の行は next_base=<行数>。
# 次に待つときは、その数を基準に渡す（待っていない間に届いた答えを取りこぼさない）。
#
# 環境変数: SETTLE（まとめて待つ秒、既定 20）、INTERVAL（見に行く間隔の秒、既定 2）
set -eu

dir=${1:?"使い方: wait_decision.sh <作業フォルダー> [基準の行数]"}
f="$dir/decisions.jsonl"
settle=${SETTLE:-20}
interval=${INTERVAL:-2}

[ -d "$dir" ] || { echo "作業フォルダーが無い: $dir" >&2; exit 2; }
[ -e "$f" ] || : >"$f"

count() { wc -l <"$f" | tr -d ' '; }

base=${2:-$(count)}
case $base in
  '' | *[!0-9]*) echo "基準の行数は数字で: $base" >&2; exit 2 ;;
esac

until [ "$(count)" -gt "$base" ]; do
  sleep "$interval"
done
sleep "$settle"

n=$(count)
sed -n "$((base + 1)),${n}p" "$f"
echo "next_base=$n"
