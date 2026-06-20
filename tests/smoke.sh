#!/usr/bin/env bash
# Curl-based smoke tests for all 4 services.
# Requires all 4 services to be running (run: make wait first).
set -euo pipefail

PASS=0
FAIL=0

# Ports match launcher.sh (read from .env via the Makefile).
DIRECTOR_PORT=${DIRECTOR_PORT:-8000}
TTS_PORT=${TTS_PORT:-8001}
SFX_PORT=${SFX_PORT:-8002}
MIXER_PORT=${MIXER_PORT:-8003}

check() {
  local name=$1; shift
  if "$@"; then
    echo "PASS: $name"
    PASS=$((PASS+1))
  else
    echo "FAIL: $name"
    FAIL=$((FAIL+1))
  fi
}

# ── Health checks ─────────────────────────────────────────────────────────────
echo "=== Health checks ==="
for port in "$DIRECTOR_PORT" "$TTS_PORT" "$SFX_PORT" "$MIXER_PORT"; do
  check "health:$port" curl -sf "http://localhost:$port/health" -o /dev/null
done

# ── Director: highlight → ScriptBlock ────────────────────────────────────────
echo ""
echo "=== Director: highlight → ScriptBlock ==="
SCRIPT_RESPONSE=$(curl -s -X POST "http://localhost:${DIRECTOR_PORT}/script" \
  -H "Content-Type: application/json" \
  -d '{
    "highlight": "I saw the old man shudder. His eye was upon me.",
    "context_chunks": [
      "The old man had never wronged me. But his eye! His vulture eye!",
      "I made up my mind to take the life of the old man."
    ],
    "book_id": "poe-the-tell-tale-heart",
    "speaker_hint": "narrator"
  }' || true)

if [[ -n "$SCRIPT_RESPONSE" ]]; then
  echo "$SCRIPT_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$SCRIPT_RESPONSE"
else
  echo "(Director returned empty — is vLLM running on port 30000?)"
fi

check "director:has_sequence_id" \
  python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert 'sequence_id' in d" "$SCRIPT_RESPONSE"
check "director:has_sfx_track" \
  python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert 'sfx_track' in d" "$SCRIPT_RESPONSE"
check "director:has_dialogue" \
  python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert len(d.get('dialogue',''))>0" "$SCRIPT_RESPONSE"

# ── TTS: dialogue → PCM ───────────────────────────────────────────────────────
echo ""
echo "=== TTS: dialogue → PCM ==="
curl -sf -X POST "http://localhost:${TTS_PORT}/synthesize" \
  -H "Content-Type: application/json" \
  -d '{
    "dialogue": "[ominously] His vulture eye fell upon me.",
    "speaker_id": "narrator_default",
    "sequence_id": "smoke_001"
  }' > /tmp/smoke_tts.pcm

PCM_BYTES=$(wc -c < /tmp/smoke_tts.pcm)
echo "PCM bytes received: $PCM_BYTES"
check "tts:nonzero_pcm" test "$PCM_BYTES" -gt 0

ffmpeg -f s16le -ar 44100 -ac 1 -i /tmp/smoke_tts.pcm /tmp/smoke_tts.wav -y -loglevel error
WAV_BYTES=$(wc -c < /tmp/smoke_tts.wav)
echo "WAV output: ${WAV_BYTES} bytes → /tmp/smoke_tts.wav"
check "tts:valid_wav" test "$WAV_BYTES" -gt 44  # WAV header is 44 bytes

# ── SFX: prompts → WAV cues ───────────────────────────────────────────────────
echo ""
echo "=== SFX: prompts → WAV cues ==="
SFX_RESPONSE=$(curl -sf -X POST "http://localhost:${SFX_PORT}/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "sequence_id": "smoke_001",
    "sfx_track": [
      {"timestamp_ms": 0, "prompt": "candle flickering in dark room"},
      {"timestamp_ms": 1500, "prompt": "slow creaking floorboard"}
    ]
  }')

# Write the response to a file: the base64 audio is far larger than the OS
# command-line arg limit, so it can't be passed as argv[1].
echo "$SFX_RESPONSE" > /tmp/smoke_sfx.json

python3 -c "
import json, base64
data = json.load(open('/tmp/smoke_sfx.json'))
print(f'Got {len(data[\"cues\"])} cues')
for c in data['cues']:
    wav = base64.b64decode(c['audio_b64'])
    print(f'  t={c[\"timestamp_ms\"]}ms  duration={c[\"duration_ms\"]}ms  wav={len(wav)//1024}KB')
"

check "sfx:two_cues" \
  python3 -c "import json; d=json.load(open('/tmp/smoke_sfx.json')); assert len(d['cues'])==2"
check "sfx:has_audio_b64" \
  python3 -c "import json; d=json.load(open('/tmp/smoke_sfx.json')); assert all(c['audio_b64'] for c in d['cues'])"

# ── Mixer: full pipeline → MP3 ───────────────────────────────────────────────
echo ""
echo "=== Mixer: ScriptBlock → MP3 ==="

# Use the ScriptBlock from the Director response (if it succeeded) or a hardcoded one
BLOCK=$(echo "$SCRIPT_RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(json.dumps(d))
except Exception:
    print(json.dumps({
        'sequence_id': 'smoke_fallback_001',
        'speaker_id': 'narrator_default',
        'dialogue': '[ominously] The eye was open, wide, wide open.',
        'sfx_track': [
            {'timestamp_ms': 0, 'prompt': 'deep ominous drone'},
            {'timestamp_ms': 2000, 'prompt': 'distant heartbeat thumping'}
        ]
    }))
")

curl -sf -X POST "http://localhost:${MIXER_PORT}/mix" \
  -H "Content-Type: application/json" \
  -d "$BLOCK" > /tmp/smoke_mix.mp3

MP3_BYTES=$(wc -c < /tmp/smoke_mix.mp3)
echo "MP3 output: ${MP3_BYTES} bytes → /tmp/smoke_mix.mp3"
check "mixer:nonzero_mp3" test "$MP3_BYTES" -gt 1024

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
