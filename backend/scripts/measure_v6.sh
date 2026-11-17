#!/bin/bash
# Đo v6 (tài liệu mục 13): mốc B′ (code v5.1 + eval mới, 3 seed) rồi C1 → C4, D1, D2 trên `dev`, model gpt-4o-mini.
# Mỗi bước chạy từ một git worktree riêng ở ../<tên> để code đang sửa không ảnh hưởng lần chạy.
#
#   bash backend/scripts/measure_v6.sh            # từ thư mục gốc repo; cần OPENAI_API_KEY còn credit trong backend/.env
#
# Kết quả: ../<worktree>/backend/runs/<id>/ ; chấm và so bằng repo chính:
#   python -m detect.eval --run <đường dẫn run> --data data/eval
#   python -m detect.compare --pair <run A> <run B> --data data/eval
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(dirname "$REPO")"
PY="$REPO/backend/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY="$REPO/backend/.venv/bin/python"
export PYTHONIOENCODING=utf-8
STEPS="base:38319ee c1:dda4e45 c2:5ba19bd c3:1718ab9 c4:feaecec d1:v6-d1 d2:v6-d2"

for s in $STEPS; do
  name="${s%%:*}"; ref="${s#*:}"; dir="$ROOT/v6m_$name"
  [ -d "$dir" ] || git -C "$REPO" worktree add -q "$dir" "$ref"
  cp "$REPO/backend/.env" "$dir/backend/.env"
  mkdir -p "$dir/backend/runs"
done

# mốc: 3 seed song song (code v5.1 không có --jobs); các bước sau tuần tự, mỗi bước --jobs 4 (tránh vượt giới hạn token / phút)
for seed in 1 2 3; do
  (cd "$ROOT/v6m_base/backend" && "$PY" -m detect.run --data data/eval --split dev --mode openai --seed $seed --id base_v51_s$seed -q \
     > runs/base_v51_s$seed.log 2>&1) &
done
(
  for name in c1 c2 c3 c4 d1 d2; do
    (cd "$ROOT/v6m_$name/backend" && "$PY" -m detect.run --data data/eval --split dev --seed 1 --jobs 4 --id ${name}_s1 -q \
       > runs/${name}_s1.log 2>&1)
    echo "$(date +%T) xong $name"
  done
) &
wait
for f in "$ROOT"/v6m_*/backend/runs/*.log; do echo "== $f"; tail -n 2 "$f"; done
