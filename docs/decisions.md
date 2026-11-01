# Decisions

One entry per choice the prompts left open: date, decision, why, alternatives.

| Date | Decision | Why |
|---|---|---|
| 2026-10-31 | Python core (FastAPI, Typer) + React/Vite cockpit | Fits LiteLLM, KTransformers, and ML tooling; cockpit uses the Pablo design system tokens |
| 2026-10-31 | Repo `pablo-harness`, CLI and package `pablo` | Descriptive repo name; short command |
| 2026-10-31 | Start fresh; port the good parts of ai-coding-team in M2 | Clean structure without losing the useful pieces |
| 2026-10-31 | Apache-2.0 | Permissive with a patent grant; compatible with the MIT/Apache tools we borrow from |
| 2026-11-01 | CUDA toolkit 13.0 from NVIDIA's apt repo, driver 580+ | PyTorch's default PyPI wheels moved to CUDA 13.0 (2.11+) and 12.8 is being dropped (2.12); 13.0 supports the 3090 (sm_86). Ubuntu's `nvidia-cuda-toolkit` is 12.0, too old. If an engine needs 12.x, install it side by side in /usr/local/cuda-12.x. |
| 2026-11-01 | Installed `cuda-toolkit-13-0` only; driver stayed `nvidia-driver-580-open` 580.95.05 | `nvcc` reports 13.0.88. The `cuda` meta-package also installs a driver, so it was not used. |
| 2026-11-01 | Models and engines go in `~/pablo-data` on `/dev/nvme0n1p2` | `/` and `$HOME` are the same ext4 on the WD Black SN7100 2 TB, with about 1.7 TB free. There is no second disk or mount. This is the default `$PABLO_DATA`. |
| 2026-11-01 | Coder is Unsloth `Qwen3.6-35B-A3B-UD-Q4_K_S` (20.9 GB, Apache-2.0). Router is Unsloth `Qwen3.5-4B-Q4_K_M` (2.74 GB, Apache-2.0), always on CPU | `Qwen/Qwen3.6-35B-A3B` is the post-trained model; there is no separate coder checkpoint. `UD-Q4_K_M` is 22.1 GB and leaves under 2 GB on a 24 GB card before the router is considered. OpenJev 4B v2 is a 3-label entailment classifier, so it cannot answer the router's JSON chat prompt. The 4B Q4 router stays resident in RAM (`-ngl 0`). |
| 2026-11-01 | Coder KV cache is `q8_0`, then `--n-cpu-moe 2`. A coder generation kept 3696 MiB free, so N stays at 2 | Order for M1: quantize K and V first (`-ctk q8_0 -ctv q8_0`), then spill MoE layers starting at 2. At N=2, `nvidia-smi` memory.free bottomed at 3696 MiB during generation (24576 MiB card, router on CPU). Tok/s are in `docs/benchmarks.md`. |
| 2026-11-01 | llama.cpp tag `b12031`, CUDA arch 86 only. Thinking off via `--reasoning off`. Lanes are systemd user units | `b12031` is the newest `bNNNN` tag and already contains `qwen35moe`. `--chat-template-kwargs enable_thinking` is deprecated in this tag; `--reasoning off` sets the same template flag. User units match the existing `pablo.service` and stay up after an SSH disconnect. |
| 2026-11-01 | LiteLLM proxy 1.106.6 in `~/pablo-data/engines/litellm`, not the harness venv | The gateway is a separate process. `telemetry: false` is set in `config/litellm.yaml`. Pyroscope profiling stays off (`LITELLM_ENABLE_PYROSCOPE=false`). The model cost map is local so startup does not fetch it. |
| 2026-11-01 | Judge candidates are Mistral-Small-3.2-24B heretic Q5_K_M (15.61 GiB, full GPU) and DeepSeek-V4-Flash REAP K128 Q2 (46.98 GiB, experts in RAM). GLM-5.2 is not downloaded | See the M3 fit math below. Q4 ~150B and the 58 GiB REAP Q2_K do not leave 8 GB RAM free. The heretic Mistral is a different family from the Qwen coder, Apache-2.0, and fits on the 3090 once the coder is stopped. `pablo eval-judge` accuracy picks the winner. |
| 2026-11-01 | Bench llama.cpp `b12031` and colibrì v2.1.0. Do not build KTransformers 0.7.2 on this box | `b12031` already has `mistral3` and `deepseek4`. colibrì v2.1.0 (Apache-2.0, 2026-10-31) reads a `deepseek4` GGUF in place; its CUDA path does not run GGUF tensors, so that number is the CPU comparison. KTransformers 0.7.2 is Apache-2.0 and wants AMX plus resident experts. This Ryzen 7 5700X is AVX2 only, and the weights that fit are GGUF. |
| 2026-11-01 | Deep lane stays math-only until the judge is committed. Smallest GLM-5.2 GGUF is about 217 GB | Disk has 1.6 TB free, so the file would fit on the NVMe. RAM cannot hold it. It only runs by streaming. |
| 2026-11-01 | The judge is Mistral-Small-3.2-24B heretic Q5_K_M on llama.cpp at 127.0.0.1:8003, with `-ngl 0` until M4 | `eval-judge` scored 15/15 after the prompt in `judge.py`. The coder baseline was 9/10. On the GPU the same file loaded in 6.0 s, decoded at about 46 tok/s, and peaked at 18439 MiB. The coder already holds about 20 GB, so the service stays on CPU (2.42 tok/s on run `350523f862`) until the swapper exists. colibrì does not serve `mistral3`. KTransformers stays unbuilt. |

### Installing CUDA 13.0 on Pablo
```bash
nvidia-smi                       # top right must say "CUDA Version: 13.0" or higher (driver 580+)
# if lower: sudo ubuntu-drivers list ; sudo apt install nvidia-driver-580 ; sudo reboot
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu$(. /etc/os-release; echo ${VERSION_ID//./})/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb && sudo apt update
sudo apt install -y cuda-toolkit-13-0      # toolkit only; the "cuda" meta-package can swap your driver
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}' >> ~/.bashrc
source ~/.bashrc && nvcc --version
```

## M2 · What came over from ai-coding-team (2026-11-01)

Built in the core track (the legacy repo is readable from GitHub, so no GPU was needed). Source:
`Mateo610/ai-coding-team-legacy`, especially `SALVAGE.md`.

| Legacy piece | Verdict | Where it lives now |
|---|---|---|
| `context_manager.detect_task_type` | adapt | `pablo/tasks.py` `detect_task_type` (adds bugfix, refactor, docs, frontend types) |
| `complexity_predictor` 1-10 score | **lost** the comparison (19/30 vs 24/30 for the M0 rule) | dropped; only its design vocabulary survives |
| `complexity_predictor` keyword lists | adapt | `tasks.deep_signals`: combined estimator scores 27/30 on `tests/data/complexity.yaml` |
| Auto-route to Claude at score >= 8 | **dropped** | violates rule 3; complexity only picks local lanes |
| `context_manager` section budgets | adapt | `pablo/context.py` (task, skill, error, tree, files under 10.5k tokens) |
| `.aiconfig/skills/*` (23 cards, ~1.7k tokens each) | adapt, trimmed | `skills/*.md`, 8 cards under 300 tokens, at most one per prompt |
| `learning/logger.py` TOON format | adapt | `pablo export-traces`: daily `@schema` stats files, no prompts or code |
| `learning/evaluator.py` blended 0-100 | drop | mixes checks, speed, and routing; M7 keeps separate signals |
| Old test generator | drop | truncated output, reported 0/0; pytest exit 5 is a failure (tested) |
| `routing_optimizer`, `error_patterns` | later | need a few dozen real runs first; failures are prompt hints, never training rows |
| Specialist agent team, Chroma RAG, 26-tool system | drop | don't fit one 3090; M6 repo map replaces RAG; coder returns edits, checks run outside |

Caveat: the 30 complexity labels were written by Claude, so the 27/30 is a sanity check, not
proof. Add your own real tasks to the YAML; the test keeps whichever estimator wins.

**Legacy data rule:** nothing in the legacy `data/` folder (TOON logs, meta JSONL, routing DB) is
ever imported into Pablo's traces or training exports. Some of those runs went to Claude
automatically and aren't reliably marked.

## M3 · Fit math (2026-11-01, before any judge download)

Measured on this boot, coder resident: RTX 3090 24576 MiB with 20439 MiB used and 3686 MiB free. RAM 62 GiB, about 11 GiB used and 51 GiB available. Disk 1.6 TB free on `/dev/nvme0n1p2`. CPU is a Ryzen 7 5700X (AVX2, no AVX-512, no AMX).

Resident ceiling, keeping 8 GiB of RAM free and about 4 GiB of VRAM for KV and compute: 62 − 11 − 8 = 43 GiB of RAM for weights, plus about 20 GiB of VRAM once the coder is stopped. A file that must sit entirely in RAM is limited to 43 GiB. A hybrid load can be larger only when the dense tensors stay on the GPU.

| Candidate | On disk | Fits? |
|---|---|---|
| DeepSeek V4 Flash REAP K160 Q4_K_M | 102 GB | No. Stream from NVMe |
| REAP 150B MXFP4 / Q3_K_M / Q2_K | 79.21 / 66.07 / 58.11 GiB | No. The 58 GiB file plus the 11 GiB already used leaves under 8 GiB even before KV |
| REAP K128 uniform Q2 (`sleepyeldrazi`, GGUF arch `deepseek4`, MIT, 1328 tensors) | 46.98 GiB | Hybrid only: attention on the GPU, routed experts in RAM. A full-RAM load leaves about 4 GiB and fails the spare rule |
| Mistral-Small-3.2-24B heretic Q5_K_M (`llmfan46`, arch `mistral3`, Apache-2.0) | 15.61 GiB | Yes, fully on the GPU with the coder stopped. Q8_0 is 23.33 GiB and leaves no KV room; Q6_K is 18.02 GiB and was the less conservative step |
| GLM-5.2 smallest Unsloth GGUF (UD-IQ1_S) | ~217 GB | Disk yes (1.6 TB free). RAM no. Stream only. Not downloaded |

The K128 header still says `expert_count = 256` and `expert_used_count = 6`. The file is 50439361920 bytes. llama.cpp `b12031` rejects it: `blk.3.ffn_gate_inp.weight` is 4096×128, and the runtime expects 4096×256. colibrì v2.1.0 imports the GGUF and then fails the same way (`layers.3.ffn.gate.weight`). It is not replaced with a larger quant.
