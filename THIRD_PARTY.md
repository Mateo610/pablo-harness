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
| Geist, Geist Mono fonts (@fontsource) | SIL OFL 1.1 | bundled font files |
| llama-swap v269 | MIT | process (lane swapper, `~/pablo-data/engines/llama-swap`) |

## Planned (verify each license at the source before adopting)
| Name | Expected license | Planned use | Milestone |
|---|---|---|---|
| LiteLLM (core) | MIT | process (gateway) | M1 |
| llama.cpp | MIT | process (engine) | M1 |
| KTransformers | Apache-2.0 | process (MoE engine) | M3 |
| colibrì v2.1.0 | Apache-2.0 | process (expert streaming) | M3 |
| Serena | MIT | process (LSP tools over MCP) | M6 |
| Langfuse (core) | MIT | process (trace UI) | M7 |
| Promptfoo | MIT | dev tool (evals) | M7 |
| Unsloth | Apache-2.0 | dev tool (LoRA training) | M8 |
| MiroFish-Offline | AGPL-3.0 | process only, never imported | later |

## Model weights (never committed; archived with scripts/archive-model.sh)
Record each model's name, source URL, license, and revision in its archive SOURCE.txt and here.
| Model | License | Lane |
|---|---|---|
| Qwen3.6-35B-A3B UD-Q4_K_S (Unsloth GGUF of `Qwen/Qwen3.6-35B-A3B`, rev `a483e9e6cbd595906af30beda3187c2663a1118c`) | Apache-2.0 | coder |
| Mistral-Small-3.2-24B-Instruct-2506 heretic Q5_K_M (`llmfan46` GGUF, Heretic abliteration of `mistralai/Mistral-Small-3.2-24B-Instruct-2506`) | Apache-2.0 | judge |
| Qwen3.5-4B Q4_K_M (Unsloth GGUF of `Qwen/Qwen3.5-4B`, rev `e87f176479d0855a907a41277aca2f8ee7a09523`) | Apache-2.0 | router (CPU) |
