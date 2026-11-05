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
3. Speech-to-text: whisper.cpp's `whisper-server` (MIT), built from a pinned tag.
   Measure two setups on 5-second and 15-second clips. Record the time to transcribe and the
   transcript quality:
   - CPU, `small.en` (or `base.en` if small is too slow).
   - GPU (CUDA build), `large-v3-turbo` quantized, while the coder is loaded. Check `nvidia-smi` headroom
     with the coder resident (~19.6 GB), and with the advisor (~17 GB) and deep loaded.
   Pick the faster setup that never pushes VRAM past ~23 GB with any lane loaded. Run it as
   `pablo-stt.service` on 127.0.0.1:8090. Set `voice.stt_url: http://127.0.0.1:8090` and
   `stt_api: whisper.cpp`.
4. Text-to-speech: a local server that speaks the OpenAI `POST /v1/audio/speech` API. First
   candidate is Kokoro-82M (weights Apache-2.0) behind an OpenAI-compatible server. Confirm the
   server's license, and that it runs on CPU without network access. If nothing fits, propose an
   alternative before installing it. Run it as `pablo-tts.service` on 127.0.0.1:8880. Set
   `voice.tts_url`, `tts_model`, and `tts_format: wav`.
5. Voices: pick a distinct voice for `coder`, `advisor`, and `deep` (`voice.voices`), so you can tell
   who's talking with your eyes closed. Matt picks; play him three options for each.
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
