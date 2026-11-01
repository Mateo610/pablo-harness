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
