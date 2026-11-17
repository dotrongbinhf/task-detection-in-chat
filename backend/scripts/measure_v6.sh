#!/bin/bash
# Đo v6 (tài liệu mục 13): mốc B′ (code v5.1 + eval mới, 3 seed) rồi C1 → C4, D1, D2.
# Mỗi bước chạy từ một git worktree riêng ở ../v6m_<tên> để code đang sửa không ảnh hưởng lần chạy.
#
#   bash backend/scripts/measure_v6.sh              # mặc định split mini_dev (rẻ, nhanh)
#   SPLIT=dev bash backend/scripts/measure_v6.sh    # cả dev
#
# Dùng model / endpoint trong backend/.env của repo chính (OpenAI hoặc tương thích OpenAI, vd. River).
# Commit cũ chưa có công tắc endpoint → vá tại chỗ trong worktree (không commit):
#   - split.json lấy từ repo chính (có mini_dev / mini_test; tin nhắn giống nhau ở mọi bước)
#   - EMBED_MODEL=none → không embedding (bước trước C3 vẫn còn BM25)
#   - retry 429 / 5xx dài hơn
#   - mốc v5.1 có cổng: GATE_MODEL = EXTRACT_MODEL
#
# Kết quả: ../v6m_<tên>/backend/runs/<tên>_<split>_s<seed>/ ; chấm và so bằng repo chính:
#   python -m detect.eval --run <đường dẫn run> --data data/eval
#   python -m detect.compare --pair <run A> <run B> --data data/eval
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(dirname "$REPO")"
PY="$REPO/backend/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY="$REPO/backend/.venv/bin/python"
export PYTHONIOENCODING=utf-8
SPLIT="${SPLIT:-mini_dev}"
STEPS="base:38319ee c1:dda4e45 c2:5ba19bd c3:1718ab9 c4:feaecec d1:v6-d1 d2:v6-d2"
EXTRACT_MODEL="$(grep -E '^EXTRACT_MODEL=' "$REPO/backend/.env" | cut -d= -f2-)"

for s in $STEPS; do
  name="${s%%:*}"; ref="${s#*:}"; dir="$ROOT/v6m_$name"
  [ -d "$dir" ] || git -C "$REPO" worktree add -q --detach "$dir" "$ref"
  git -C "$dir" checkout -q -- backend/detect/llm.py backend/data/eval/split.json
  cp "$REPO/backend/data/eval/split.json" "$dir/backend/data/eval/split.json"
  sed -i -e 's/self\.embedder = OpenAIEmbedder(/self.embedder = None if (os.environ.get("EMBED_MODEL") or "").lower() == "none" else OpenAIEmbedder(/' \
         -e 's/retries: int = 3/retries: int = 8/' -e 's/time\.sleep(0\.5 \* 2\*\*i)/time.sleep(min(30.0, 1.0 * 2**i))/' \
         "$dir/backend/detect/llm.py"
  cp "$REPO/backend/.env" "$dir/backend/.env"
  [ "$name" = base ] && [ -n "$EXTRACT_MODEL" ] && printf '\nGATE_MODEL=%s\n' "$EXTRACT_MODEL" >> "$dir/backend/.env"
  mkdir -p "$dir/backend/runs"
done

# mốc: 3 seed nối tiếp (code v5.1 không có --jobs; lời gọi cổng không có seed → chạy song song sẽ ghi đè cùng file cache)
(
  for seed in 1 2 3; do
    (cd "$ROOT/v6m_base/backend" && "$PY" -m detect.run --data data/eval --split "$SPLIT" --mode openai --seed $seed \
       --id base_${SPLIT}_s$seed -q > runs/base_${SPLIT}_s$seed.log 2>&1)
    echo "$(date +%T) xong base s$seed"
  done
) &
# các bước sau nối tiếp, mỗi bước --jobs 4 (tránh vượt giới hạn token / phút)
(
  for name in c1 c2 c3 c4 d1 d2; do
    (cd "$ROOT/v6m_$name/backend" && "$PY" -m detect.run --data data/eval --split "$SPLIT" --seed 1 --jobs 4 \
       --id ${name}_${SPLIT}_s1 -q > runs/${name}_${SPLIT}_s1.log 2>&1)
    echo "$(date +%T) xong $name"
  done
) &
wait
for f in "$ROOT"/v6m_*/backend/runs/*_${SPLIT}_s*.log; do echo "== $f"; tail -n 2 "$f"; done
