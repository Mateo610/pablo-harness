# Third-party software and models

Pablo is Apache-2.0. This file records everything it uses or borrows from, under which license,
and how it is used. Add a row before adding a dependency or copying code.

**How it may be used** · `dep` installed dependency · `copied` code copied in with notice ·
`process` separate process (required for AGPL/GPL) · `weights` model run locally, not distributed

## Software in the repo today
| Name | License | Use |
|---|---|---|
| FastAPI, Starlette | MIT / BSD-3 | dep |
| Uvicorn | BSD-3 | dep |
| httpx | BSD-3 | dep |
| Pydantic | MIT | dep |
| PyYAML | MIT | dep |
| Typer, Rich | MIT | dep |
| Textual | MIT | dep (terminal client) |
| React, React DOM | MIT | dep (web) |
| cmdk | MIT | dep (web) |
| Vite | MIT | dev dep (web) |
| Chakra Petch, IBM Plex Mono fonts (@fontsource) | SIL OFL 1.1 | bundled font files (no font CDN) |
| llama-swap v269 | MIT | process (lane swapper, `~/pablo-data/engines/llama-swap`) |
| Podman 4.9.3 (rootless) | Apache-2.0 | process (check sandbox) |
| gVisor `runsc` 20261026.0 | Apache-2.0 | process (OCI runtime; wrapper adds `--ignore-cgroups`) |
| `python:3.11-slim` `@sha256:673719e071ec…` | PSF + Debian packages | base of `pablo-check:py311`, saved at `~/pablo-data/images/pablo-check-py311.tar` |
| git (Debian package in `pablo-check:py311`) | GPL-2.0 | process (the harness tests run `git init` inside the sandbox; not imported) |
| tree-sitter (py-tree-sitter) 0.26.0 | MIT | dep (repo map parser) |
| tree-sitter-python 0.25.0 | MIT | dep (Python grammar for the repo map) |

## Planned (verify each license at the source before adopting)
| Name | Expected license | Planned use | Milestone |
|---|---|---|---|
| LiteLLM (core) | MIT | process (gateway) | M1 |
| llama.cpp | MIT | process (engine) | M1 |
| KTransformers | Apache-2.0 | process (MoE engine) | M3 |
| colibrì v2.1.0 | Apache-2.0 | process (expert streaming) | M3 |
| Serena (application) | MIT per pkg.go.dev (v0.1.4, Aug 2025); recorded as GPL-3.0 in M6, confirm the current LICENSE file before adopting | process only over MCP/CLI, never imported | M6 optional, not adopted |
| SolidLSP (Serena's LSP client) | MIT | planned process backend | M6 optional |
| Langfuse (core) | MIT | process (trace UI) | M7b |
| Promptfoo | MIT | dev tool (evals) | M7 |
| Unsloth | Apache-2.0 | process (LoRA training in `$PABLO_DATA/engines/unsloth`; `UNSLOTH_DISABLE_STATISTICS=1`) | M8 |
| whisper.cpp v1.9.5 (`d1be6fde11ac6e0407606b4e42fe72d34add8037`) | MIT (LICENSE file in the tag) | process (`whisper-server` on 127.0.0.1:8090, CPU) | M9b |
| MiroFish-Offline | AGPL-3.0 | process only, never imported | later |

## Model weights (never committed; archived with scripts/archive-model.sh)
Record each model's name, source URL, license, and revision in its archive SOURCE.txt and here.
| Model | License | Lane |
|---|---|---|
| Qwen3.6-35B-A3B UD-Q4_K_S (Unsloth GGUF of `Qwen/Qwen3.6-35B-A3B`, rev `a483e9e6cbd595906af30beda3187c2663a1118c`) | Apache-2.0 | coder |
| Mistral-Small-3.2-24B-Instruct-2506 heretic Q5_K_M (`llmfan46` GGUF, Heretic abliteration of `mistralai/Mistral-Small-3.2-24B-Instruct-2506`) | Apache-2.0 | judge |
| gpt-oss-120b MXFP4 (`ggml-org/gpt-oss-120b-GGUF` of `openai/gpt-oss-120b`, rev `b5c939de8f754692c1647ca79fbf85e8c1e70f8a`) | Apache-2.0 | deep |
| Qwen3.5-4B Q4_K_M (Unsloth GGUF of `Qwen/Qwen3.5-4B`, rev `e87f176479d0855a907a41277aca2f8ee7a09523`) | Apache-2.0 | router (CPU) |
| Mistral-Small-3.2-24B-Instruct-2506 Q5_K_M (Unsloth GGUF of `mistralai/Mistral-Small-3.2-24B-Instruct-2506`, GGUF rev `b750ec2299225e492f1bd27cab88a0a595fa848f`, upstream rev `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`) | Apache-2.0 | archived bake-off loser (`~/pablo-data/archive/mistral-small-3.2-24b`); not served |
| gpt-oss-20b MXFP4 (`ggml-org/gpt-oss-20b-GGUF` of `openai/gpt-oss-20b`, GGUF rev `15dcfc7e7c608c6161507211ed43c5b590801b4b`, upstream rev `6cee5e81ee83917806bbde320786a8fb61efebee`) | Apache-2.0 | archived bake-off loser (`~/pablo-data/archive/gpt-oss-20b`); not served |
| Qwen3.8-27B UD-Q4_K_M (Unsloth GGUF of `Qwen/Qwen3.8-27B`, GGUF rev `4ca720788d1e01f1bff70c033e0d0028fd02e502`, upstream rev `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`) | Apache-2.0 | judge (`pablo/judge` → `judge-qwen-dense`) |
| Whisper `ggml-small.en.bin` (`ggerganov/whisper.cpp`, repo rev `5359861c739e955e79d9a303bcbc70fb988958b1`) | MIT | speech-to-text (served). `ggml-large-v3-turbo-q5_0.bin` from the same repo is archived, not served |
