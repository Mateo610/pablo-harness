# Telemetry audit (M10 step 5)

Checked 2026-11-04. Nothing in the harness phones home.

| Surface | Finding | Action |
|---|---|---|
| LiteLLM | `config/litellm.yaml` has `telemetry: false`. Gateway unit sets `LITELLM_TELEMETRY=False`, `DISABLE_TELEMETRY=1`, `LITELLM_ENABLE_PYROSCOPE=false`, `LITELLM_LOCAL_MODEL_COST_MAP=True` (no cost-map fetch) | already off |
| Web cockpit | `@fontsource/geist` bundled locally. Built `web/dist` only contains `w3.org` / `reactjs.org` documentation URL *strings* — no CDN, analytics, or Google Fonts fetches | none |
| Unsloth | `get_statistics` downloads `unslothai/<env>` READMEs from Hugging Face unless disabled | `UNSLOTH_DISABLE_STATISTICS=1` in `scripts/train_lora.py` and `pablo-learn.service` |
| Repo grep | No Sentry / Posthog / Segment / Mixpanel / gtag | none |
| whisper.cpp v1.9.5 (`d1be6fd`) | No telemetry, analytics, or update-check calls in the server or build. The model download script fetches Hugging Face once, at install time | none |

Re-check when adding a dependency that mentions telemetry in its README.
