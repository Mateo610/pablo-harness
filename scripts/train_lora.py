#!/usr/bin/env python3
"""Train a LoRA for the coder base. Runs inside $PABLO_DATA/engines/unsloth.

Input: JSONL with {messages, lane}. Refuses any row whose lane is `claude`.
Dedupe by prompt hash, hold out 10%, save PEFT adapter under --out, then try
to convert it to a GGUF LoRA for llama-server `--lora`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path

ESCALATION = "claude"
HOLDOUT = 0.10
DEFAULT_MODEL = os.environ.get("PABLO_TRAIN_MODEL", "unsloth/Qwen3.6-35B-A3B")
# 35B-A3B Q4 fills ~23 GiB on a 3090; short seq leaves room for the forward.
MAX_SEQ = int(os.environ.get("PABLO_TRAIN_MAX_SEQ", "256"))
LORA_RANK = int(os.environ.get("PABLO_TRAIN_RANK", "8"))
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")


def _die(msg: str, code: int = 2) -> None:
    print(f"train_lora: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def prompt_hash(messages: list[dict]) -> str:
    prompt = "\n".join(f"{m.get('role')}:{m.get('content')}" for m in messages[:-1])
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def prepare(rows: list[dict], seed: int = 7) -> tuple[list[dict], list[dict]]:
    bad = [r for r in rows if str(r.get("lane") or "") in (ESCALATION, "claude")]
    if bad:
        _die(f"refusing to train: {len(bad)} row(s) from the escalation lane")
    seen: set[str] = set()
    unique: list[dict] = []
    for row in rows:
        messages = list(row.get("messages") or [])
        if len(messages) < 2:
            continue
        h = prompt_hash(messages)
        if h in seen:
            continue
        seen.add(h)
        unique.append({"messages": messages, "prompt_hash": h, "lane": row.get("lane", "")})
    if not unique:
        _die("no usable training rows")
    rng = random.Random(seed)
    rng.shuffle(unique)
    n_hold = max(1, int(len(unique) * HOLDOUT)) if len(unique) >= 10 else 0
    return unique[n_hold:], unique[:n_hold]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def train(train_rows: list[dict], out_dir: Path, model_name: str) -> Path:
    # Imported only inside the Unsloth venv; harness never imports this.
    import torch
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastModel
    from unsloth.chat_templates import get_chat_template

    print(f"train_lora: base={model_name} rows={len(train_rows)} seq={MAX_SEQ} rank={LORA_RANK}")
    print(f"train_lora: cuda={torch.cuda.is_available()} "
          f"free_gib={torch.cuda.mem_get_info()[0] / 1e9:.1f}" if torch.cuda.is_available() else
          "train_lora: cuda=False")

    # MoE + BitsandBytes 4-bit is still rough; Unsloth docs prefer bf16 LoRA for MoE.
    # Try 4-bit first (fits a 3090 for A3B-class MoE); fall back to bf16 on failure.
    model = None
    tokenizer = None
    last_err: Exception | None = None
    for fourbit in (True, False):
        try:
            model, tokenizer = FastModel.from_pretrained(
                model_name=model_name,
                max_seq_length=MAX_SEQ,
                load_in_4bit=fourbit,
                load_in_8bit=False,
                full_finetuning=False,
            )
            print(f"train_lora: loaded load_in_4bit={fourbit}")
            break
        except Exception as exc:  # noqa: BLE001 — surface and try the other path
            last_err = exc
            print(f"train_lora: load_in_4bit={fourbit} failed: {exc}")
            model = None
    if model is None or tokenizer is None:
        _die(f"could not load {model_name}: {last_err}")

    for tmpl in ("qwen3-thinking", "qwen-2.5", None):
        try:
            if tmpl is None:
                break
            tokenizer = get_chat_template(tokenizer, chat_template=tmpl)
            print(f"train_lora: chat_template={tmpl}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"train_lora: chat_template={tmpl} skipped: {exc}")

    torch.cuda.empty_cache()
    # Attention-only LoRA: expert targets on every MoE layer left ~65 MiB free on a 3090.
    model = FastModel.get_peft_model(
        model,
        r=LORA_RANK,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_alpha=LORA_RANK * 2,
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )
    print(f"train_lora: peft attached; free_gib={torch.cuda.mem_get_info()[0] / 1e9:.1f}")

    def formatting(examples: dict) -> dict:
        texts = []
        for msgs in examples["messages"]:
            text = tokenizer.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=False
            )
            # Hard-cap characters so tokenization cannot sneak past max_length.
            texts.append(text[: MAX_SEQ * 4])
        return {"text": texts}

    ds = Dataset.from_list([{"messages": r["messages"]} for r in train_rows])
    ds = ds.map(formatting, batched=True, remove_columns=ds.column_names)

    out_dir.mkdir(parents=True, exist_ok=True)
    args = SFTConfig(
        output_dir=str(out_dir / "checkpoints"),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=1,
        num_train_epochs=1,
        learning_rate=2e-4,
        logging_steps=1,
        save_strategy="no",
        max_length=MAX_SEQ,
        dataset_text_field="text",
        packing=False,
        report_to="none",
        seed=3407,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
    )
    trainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=ds, args=args)
    trainer.train()
    adapter = out_dir / "adapter"
    adapter.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(adapter))
    tokenizer.save_pretrained(str(adapter))
    meta = {
        "base_model": model_name,
        "max_seq": MAX_SEQ,
        "lora_rank": LORA_RANK,
        "train_rows": len(train_rows),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"train_lora: wrote PEFT adapter to {adapter}")
    return adapter


def convert_gguf(adapter: Path, out_dir: Path) -> Path | None:
    """Best-effort PEFT → GGUF LoRA via llama.cpp's convert_lora_to_gguf.py."""
    llama = Path(os.environ.get("PABLO_DATA", Path.home() / "pablo-data")) / "engines" / "llama.cpp"
    script = llama / "convert_lora_to_gguf.py"
    if not script.is_file():
        print("train_lora: convert_lora_to_gguf.py missing; skip GGUF", file=sys.stderr)
        return None
    gguf = out_dir / "adapter.gguf"
    cmd = [sys.executable, str(script), "--outfile", str(gguf), str(adapter)]
    print("train_lora: converting", " ".join(cmd))
    import subprocess

    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        print("train_lora: GGUF convert failed; PEFT adapter still saved", file=sys.stderr)
        return None
    print(f"train_lora: wrote {gguf}")
    return gguf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="training JSONL")
    parser.add_argument("--out", type=Path, required=True, help="adapter output dir")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--skip-train", action="store_true", help="prepare only (tests)")
    args = parser.parse_args()

    rows = load_rows(args.data)
    train_rows, holdout = prepare(rows, seed=args.seed)
    write_jsonl(args.out / "train.jsonl", train_rows)
    write_jsonl(args.out / "holdout.jsonl", holdout)
    print(f"train_lora: prepared train={len(train_rows)} holdout={len(holdout)}")
    if args.skip_train:
        return
    adapter = train(train_rows, args.out, args.model)
    convert_gguf(adapter, args.out)


if __name__ == "__main__":
    main()
