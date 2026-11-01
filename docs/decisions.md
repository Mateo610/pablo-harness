# Decisions

One entry per choice the prompts left open: date, decision, why, alternatives.

| Date | Decision | Why |
|---|---|---|
| 2026-10-31 | Python core (FastAPI, Typer) + React/Vite cockpit | Fits LiteLLM, KTransformers, and ML tooling; cockpit uses the Pablo design system tokens |
| 2026-10-31 | Repo `pablo-harness`, CLI and package `pablo` | Descriptive repo name; short command |
| 2026-10-31 | Start fresh; port the good parts of ai-coding-team in M2 | Clean structure without losing the useful pieces |
| 2026-10-31 | Apache-2.0 | Permissive with a patent grant; compatible with the MIT/Apache tools we borrow from |
| 2026-11-01 | CUDA toolkit 13.0 from NVIDIA's apt repo, driver 580+ | PyTorch's default PyPI wheels moved to CUDA 13.0 (2.11+) and 12.8 is being dropped (2.12); 13.0 supports the 3090 (sm_86). Ubuntu's `nvidia-cuda-toolkit` is 12.0, too old. If an engine needs 12.x, install it side by side in /usr/local/cuda-12.x. |

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
