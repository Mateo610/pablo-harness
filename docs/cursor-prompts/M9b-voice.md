# M9b · Voice on Pablo: speech-to-text, text-to-speech, HTTPS

**Context.** The harness side of voice is built and tested (`src/pablo/voice.py`, `/api/voice/turn`,
the Voice page). A voice turn is a chat turn: the browser records 16 kHz mono WAV, Pablo
transcribes it, the chosen model (orchestrator = coder, advisor, or deep) answers in "spoken" style,
and each sentence is synthesized while the next is still being written. Replies stream back as
NDJSON with base64 audio per sentence. `RUN:` lines still start runs you approve. No audio is
stored: the transcript lands in a conversation, the recording doesn't.

What's missing is the two engines on Pablo, and HTTPS: browsers only allow the microphone on
HTTPS (or localhost).

**Rules for this milestone**
- Both engines are separate local processes bound to 127.0.0.1. `voice.stt_url` / `tts_url`
  refuse anything that isn't loopback or the tailnet, so a cloud API can't be configured by mistake.
- Download weights ahead of time, run with `HF_HUB_OFFLINE=1` (or the engine's equivalent), and
  archive them like every model (`scripts/archive-model.sh`, `SOURCE.txt`, `THIRD_PARTY.md` row).
- Check every engine's license before you install it: Apache-2.0 or MIT preferred. GPL engines are fine
  only as separate processes (rule 5). Record what you find; don't guess from the README badge.
- No telemetry: grep each engine for analytics or update checks, and add to `docs/telemetry.md`.

**Steps**
1. `git pull`, `uv sync`, `cd web && npm ci && npm run build`.
2. HTTPS. In the Tailscale admin, turn on MagicDNS and HTTPS certificates. Then `uv run pablo cert`
   (if it says permission denied, run the printed `sudo tailscale cert …` and `chown` once).
   Restart `pablo serve --host $(tailscale ip -4)`. Open `https://pablo.<tailnet>.ts.net:8710` from
   the laptop and confirm `pablo whoami --url https://pablo.<tailnet>.ts.net:8710` says owner.
   Add a monthly systemd timer that runs `pablo cert` (certs last about 90 days) and restarts serve.
3. Speech-to-text: whisper.cpp's `whisper-server` (MIT), built from a pinned tag, **on the CPU**.
   The GPU's spare room goes to the voice. Measure `small.en` and `large-v3-turbo` (quantized) on
   5-second and 15-second clips with code words in them ("rename load_line in ledger").
   Pablo now sends a `prompt` with the repo's file and function names. Note whether the names come
   back spelled right. Pick turbo if it transcribes a 5 s clip in under ~1 s on the cores you give it,
   else small.en. Run it as `pablo-stt.service` on 127.0.0.1:8090. Set
   `voice.stt_url: http://127.0.0.1:8090` and `stt_api: whisper.cpp`.
4. Text-to-speech: **Qwen3-TTS** (Apache-2.0; Matt picked it by ear), served by an
   OpenAI-compatible server on `POST /v1/audio/speech`. The first candidate is
   `groxaxo/Qwen3-TTS-Openai-Fastapi` (Apache-2.0 additions; confirm, and pin a commit). Check that it
   makes no network calls once the weights are downloaded, then archive the weights.
   - Start with **0.6B CustomVoice on the GPU**. Measure VRAM with the coder resident (~19.6 GB) and
     with the advisor (~17 GB). Measure the time to synthesize a 12-word sentence.
   - Then try **1.7B CustomVoice**. Keep it only if it fits under ~23 GB total with the coder loaded,
     and it isn't slower to first audio.
   - With deep loaded there's probably no room. Check it. If it doesn't fit, voice with deep falls
     back to the CPU build of 0.6B (measure it) or isn't offered. Report which.
   - FlashAttention is optional. Its build needs a lot of RAM (`MAX_JOBS=4`), so only build it if
     it measurably helps.
   Run it as `pablo-tts.service` on 127.0.0.1:8880. Set `voice.tts_url`, `tts_model`, and
   `tts_format: wav`.
5. Voices:
   - Pick a distinct preset for `coder`, `advisor`, and `deep` (`voice.voices`, e.g. the English
     presets). Matt picks; play him the same sentence in each.
   - **Signature voice (optional, once):** with the **1.7B VoiceDesign** model, offline, generate a
     voice from a written description Matt writes. It must be an original voice, not a real person's.
     Save a 3–10 s clip, register it as a clone profile on the server (served by **0.6B Base**), and
     use `clone:<name>` for the orchestrator. Only Matt's own voice, or someone who agreed, may be
     cloned. Unload VoiceDesign when done.
6. CPU placement: the sandbox owns threads 12–15, and llama.cpp uses the first cores. Pin STT and TTS
   with `taskset` (or `CPUAffinity=` in the units) so a voice turn doesn't slow a deep run, or the
   reverse. Write down which threads you used and why.
7. Measure the whole loop from the browser with real models. The time is from releasing the button
   to hearing the first word. Run 5 turns each:
   - Orchestrator (coder loaded).
   - Advisor (includes the ~4 s swap the first time).
   - Deep (expect long; record it anyway).
   Report median and worst. Target for the orchestrator: first word in under 2.5 s.
   If it's slower, say which step is slow (STT, first sentence from the model, or TTS) before you tune.
8. `docs/decisions.md` row with the engines, versions, licenses, thread pinning, and the latency table.
   Update `BUILD_PLAN.md` M9b. Run `uv run ruff format src tests` and confirm CI passes.

**Don't**
- Don't use any cloud speech API, even to compare.
- Don't save recordings. If you need sample clips for measuring, keep them outside the repo and
  delete them when you're done.
- Don't change `/api/voice/turn` or the prompt. If something in the harness gets in the way, write it
  up for Claude.
