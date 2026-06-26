#!/usr/bin/env bash
# Rychlý test dabingu: dabtest.sh <src_file> <src_lang> <tgt_lang> <engine> <llm_correct> <outname>
set -e
ROOT=/o/ALLDUB
SRC="$1"; SLANG="${2:-cs-CZ}"; TLANG="${3:-cs-CZ}"; ENG="${4:-xtts}"; LLM="${5:-false}"; OUT="${6:-test}"
cd "$ROOT"
JOB=$(curl -s -F "file=@$SRC" http://127.0.0.1:8790/api/upload | python -c "import sys,json;print(json.load(sys.stdin)['job_id'])")
echo "job=$JOB  ($SLANG->$TLANG, $ENG, llm=$LLM)"
curl -s -X POST "http://127.0.0.1:8790/api/dub/$JOB" -H "Content-Type: application/json" \
  -d "{\"source_lang\":\"$SLANG\",\"target_lang\":\"$TLANG\",\"tts_engine\":\"$ENG\",\"voice\":null,\"audio_mode\":\"replace\",\"burn_subs\":false,\"llm_correct\":$LLM}" >/dev/null
last=""
for i in $(seq 1 80); do
  st=$(curl -s "http://127.0.0.1:8790/api/jobs/$JOB" | python -c "import sys,json;d=json.load(sys.stdin);print(d['status'],str(d.get('progress'))+'%',(d.get('error') or '')[:90])" 2>/dev/null)
  [ "$st" != "$last" ] && { echo "  $st"; last="$st"; }
  echo "$st" | grep -qE "^(done|error)" && break
  sleep 4
done
echo "--- bloky/ratia ---"; curl -s "http://127.0.0.1:8790/api/jobs/$JOB/log" | grep -oE "Sloučeno.*bloků|segment [0-9]+: klip [0-9.]+×"
echo "--- text do TTS ---"; curl -s "http://127.0.0.1:8790/api/jobs/$JOB" | python -c "import sys,json;print(json.load(sys.stdin).get('text_preview',''))"
OUTF=$(curl -s "http://127.0.0.1:8790/api/jobs/$JOB" | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('output_video') or d.get('output_audio') or '')")
if [ -n "$OUTF" ]; then
  tools/ffmpeg/ffmpeg.exe -y -hide_banner -loglevel error -i "$OUTF" -ar 16000 -ac 1 -c:a pcm_s16le /tmp/_dt.wav
  echo "--- dub REÁLNĚ říká (whisper medium) ---"
  ./.venv/Scripts/python.exe -c "
from faster_whisper import WhisperModel
m=WhisperModel('medium',device='cpu',compute_type='int8')
segs,info=m.transcribe(r'C:\Users\Petr\AppData\Local\Temp\_dt.wav',language='cs',beam_size=5)
print(' '.join(s.text.strip() for s in segs))" 2>&1 | tail -1
  cp "$OUTF" "/c/Users/Petr/Downloads/$OUT" 2>/dev/null && echo "kopie: Downloads/$OUT"
fi
curl -s -X DELETE "http://127.0.0.1:8790/api/jobs/$JOB" >/dev/null
