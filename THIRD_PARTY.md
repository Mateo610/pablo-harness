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
| React, React DOM | MIT | dep (web) |
| cmdk | MIT | dep (web) |
| Vite | MIT | dev dep (web) |
| Geist, Geist Mono fonts (@fontsource) | SIL OFL 1.1 | bundled font files |

## Planned (verify each license at the source before adopting)
| Name | Expected license | Planned use | Milestone |
|---|---|---|---|
| LiteLLM (core) | MIT | process (gateway) | M1 |
| llama.cpp | MIT | process (engine) | M1 |
| llama-swap | MIT | process (lane swapper) | M4 |
| KTransformers | Apache-2.0 | process (MoE engine) | M3 |
| colibrì | check LICENSE | process (expert streaming) | M3 |
| Serena | MIT | process (LSP tools over MCP) | M6 |
| Langfuse (core) | MIT | process (trace UI) | M7 |
| Promptfoo | MIT | dev tool (evals) | M7 |
| Unsloth | Apache-2.0 | dev tool (LoRA training) | M8 |
| MiroFish-Offline | AGPL-3.0 | process only, never imported | later |

## Model weights (never committed; archived with scripts/archive-model.sh)
Record each model's name, source URL, license, and revision in its archive SOURCE.txt and here.
| Model | License | Lane |
|---|---|---|
| _fill in at M1_ | | |
