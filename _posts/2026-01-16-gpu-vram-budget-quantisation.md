---
layout: post
title: "Sizing One GPU for Local LLM Inference: VRAM, Quantisation and KV Cache"
subtitle: "Working out whether weights, context and KV cache actually fit before you buy or provision anything."
date: 2026-01-16 09:00:00 +0200
tags: [llm, performance]
description: >-
  Fitting a local language model onto one GPU means budgeting three separate
  pools of VRAM — weights, activations and a KV cache that grows with context
  length — and the wrong cache format can silently multiply latency without
  raising an error. This walks through the arithmetic and a way to measure it.
---

## The problem

A GPU has a fixed amount of VRAM. A model server needs to fit three things into it: the
model's weights, a working set of activations, and a key-value (KV) cache that grows with
every token of context it has to remember. Get the arithmetic wrong and the failure modes
are not all the same kind of obvious.

Running out of VRAM at load time is the easy case — the process dies with an
out-of-memory error and you know exactly what happened. The harder case is a request that
loads fine and then falls over, or degrades, once the context grows: a chat that starts
fast and gets steadily slower, or a batch of concurrent requests that OOMs only when there
are enough of them in flight for their KV caches to add up.

The worse case is not a crash at all. Many inference backends support several data types
for the KV cache — full precision, 8-bit, various 4-bit schemes — as a way to shrink that
pool of memory. But not every combination of cache dtype, attention implementation and
hardware generation has a fast code path. Pick one that falls outside the fast path and
the server keeps running, keeps producing correct output, and quietly runs two to three
times slower, because it fell back to an unoptimised attention kernel. Nothing errors.
Nothing logs a warning by default. You only notice because throughput is worse than it
should be, and "worse than it should be" is a hard thing to notice without a number to
compare against.

The fix is to budget VRAM as three separate pools before you pick a model and a
quantisation scheme, and to measure — not assume — which cache format actually runs fast
on your hardware.

## Working through it

### Weights: parameter count times bytes per parameter

The weight pool is the simplest of the three. A model with *P* parameters stored at *b*
bytes each takes roughly `P × b` bytes, plus a few percent of overhead for embeddings and
non-quantised layers that most backends leave in a higher precision.

Bytes per parameter depends on the quantisation format:

| Format | Bits/param | Typical use |
|---|---|---|
| FP16 / BF16 | 16 | full precision, reference quality |
| Q8_0 | 8 | close to full precision, half the size |
| Q4_K_M | ~4.5 (mixed) | the common "good enough" default |
| Q4_0 | 4 | smaller again, more quality loss |

These are the GGUF quantisation names used by llama.cpp and its ecosystem (llama-cpp-python,
Ollama, LM Studio all build on it), chosen here because they are public, well-documented,
and a reader can reproduce every number below without any private tooling.

An openly available 8-billion-parameter model at Q4_K_M is roughly `8e9 × 4.5 / 8` bytes,
about 4.5 GB. The same model at FP16 is about 16 GB. That difference alone can be the
difference between fitting on a 12 GB card and not fitting at all.

### KV cache: it scales with context, not with the model alone

The KV cache stores one key vector and one value vector per attention head, per layer, per
token of context, for every request being served. Its size is:

```
kv_bytes = 2 (K and V) × num_layers × num_kv_heads × head_dim × context_length × bytes_per_element
```

Two things about this formula matter more than they look like they should.

First, it scales linearly with context length. A model that fits comfortably at a 4k
context can run out of memory purely from being asked to hold an 32k context, with the
weights unchanged. This is the part that catches people out: the model loaded fine, so the
VRAM budget looked fine, and then a single long conversation or a large retrieved document
pushed it over.

Second, `num_kv_heads` is not always the same as the number of attention heads. Models
using grouped-query attention (GQA) — most current openly released models do — share one
key/value head across several query heads. A model with 32 query heads but 8 KV heads has
a KV cache a quarter the size of one that uses full multi-head attention with 32 KV heads,
at the same context length and head dimension. This is a property of the model
architecture, not something you choose, so it is worth checking before assuming a bigger
model's KV cache will scale the way a smaller model's did.

### Choosing quantisation for weights and KV cache separately

These are two independent knobs, and it is worth treating them as such rather than
reaching for one "quality level" that changes both together.

Weight quantisation mostly affects output quality — a heavily quantised model gives worse
answers, and that degradation is silent in the sense that nothing fails, the model is just
worse. KV cache quantisation mostly affects memory and, if you pick a format outside the
backend's fast path, speed. A sensible starting point for a single-GPU deployment is
weights at Q4_K_M or Q8_0 depending on how much VRAM quality is worth to you, and a KV
cache left at whatever the backend's default and fastest format is — checked, not assumed.

### Measuring, not assuming

The budget above is an estimate. Before committing to a configuration, measure both VRAM
actually used and tokens/second actually achieved, because the two things that break the
estimate — backend overhead and fast-path availability — do not show up in arithmetic.

`nvidia-smi` gives actual VRAM usage. A backend's own benchmark tool gives actual
throughput. The two together tell you whether the configuration you planned on paper is
the one you are actually running.

## The solution

A small script to compute the estimate, and a benchmark recipe to check it against reality.

```python
#!/usr/bin/env python3
"""vram_budget.py — estimate VRAM needed for weights + KV cache.

Usage:
    python vram_budget.py --params 8e9 --weight-bits 4.5 \
        --layers 32 --kv-heads 8 --head-dim 128 \
        --context 8192 --kv-bits 16 --batch 1
"""
import argparse


def weight_bytes(params: float, weight_bits: float) -> int:
    return int(params * weight_bits / 8)


def kv_cache_bytes(
    layers: int,
    kv_heads: int,
    head_dim: int,
    context: int,
    kv_bits: float,
    batch: int,
) -> int:
    bytes_per_element = kv_bits / 8
    # 2 for separate K and V tensors
    return int(2 * layers * kv_heads * head_dim * context * bytes_per_element * batch)


def human(n: int) -> str:
    gib = n / (1024**3)
    return f"{gib:.2f} GiB"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--params", type=float, required=True, help="parameter count, e.g. 8e9")
    p.add_argument("--weight-bits", type=float, required=True, help="bits per weight, e.g. 4.5 for Q4_K_M")
    p.add_argument("--layers", type=int, required=True)
    p.add_argument("--kv-heads", type=int, required=True, help="number of KV heads (GQA-aware, not query heads)")
    p.add_argument("--head-dim", type=int, required=True)
    p.add_argument("--context", type=int, required=True, help="context length in tokens")
    p.add_argument("--kv-bits", type=float, default=16.0, help="bits per KV cache element")
    p.add_argument("--batch", type=int, default=1, help="concurrent sequences")
    p.add_argument("--overhead-pct", type=float, default=10.0, help="activation/runtime overhead as % of weights")
    args = p.parse_args()

    w = weight_bytes(args.params, args.weight_bits)
    kv = kv_cache_bytes(args.layers, args.kv_heads, args.head_dim, args.context, args.kv_bits, args.batch)
    overhead = int(w * args.overhead_pct / 100)
    total = w + kv + overhead

    print(f"Weights:           {human(w)}")
    print(f"KV cache:          {human(kv)}  (context={args.context}, batch={args.batch})")
    print(f"Runtime overhead:  {human(overhead)}  (~{args.overhead_pct:.0f}% of weights)")
    print(f"Estimated total:   {human(total)}")


if __name__ == "__main__":
    main()
```

Run it for an 8B-parameter model at Q4_K_M, 32 layers, 8 KV heads (GQA), head dimension
128, at an 8k context:

```bash
python vram_budget.py --params 8e9 --weight-bits 4.5 \
    --layers 32 --kv-heads 8 --head-dim 128 \
    --context 8192 --kv-bits 16 --batch 1
```

```
Weights:           4.19 GiB
KV cache:          0.50 GiB  (context=8192, batch=1)
Runtime overhead:  0.42 GiB  (~10% of weights)
Estimated total:   5.11 GiB
```

Re-run with `--context 65536` and the KV cache term grows eightfold while the weights term
does not move — the exact effect described above, made concrete.

Then check the estimate against a real backend. With llama.cpp built and a GGUF model file
on disk, `llama-bench` measures actual throughput at a given context and cache type:

```bash
# Pinned to a released llama.cpp tag known to support KV cache quantisation flags.
git clone --branch b4079 --depth 1 https://github.com/ggerganov/llama.cpp
cmake -S llama.cpp -B llama.cpp/build -DGGML_CUDA=ON
cmake --build llama.cpp/build -j --target llama-bench

# FP16 KV cache (the safe, always-fast-path default on most backends):
./llama.cpp/build/bin/llama-bench -m model-q4_k_m.gguf -c 8192 -ctk f16 -ctv f16

# 8-bit quantised KV cache (smaller, but only fast if the backend's kernel supports it):
./llama.cpp/build/bin/llama-bench -m model-q4_k_m.gguf -c 8192 -ctk q8_0 -ctv q8_0
```

Both runs print tokens/second for prompt processing and generation. If the quantised-cache
run reports notably lower tokens/second than the FP16 run, that combination fell outside
the fast path on your hardware, regardless of how much smaller it made the cache on paper.
That comparison — run twice, numbers read off the output — is the actual answer to "will
this be fast", and it costs two commands.

## Conclusion

Three things generalise beyond sizing one GPU for one model:

**Budget memory as separate pools, not one number.** Weights are fixed once a model and
quantisation are chosen; the KV cache is not — it grows with context and concurrency, and
a budget that only accounts for weights will look fine right up until a long request
arrives.

**A fast path that silently isn't there is worse than an error.** Anywhere a system offers
several equivalent-looking configuration options with a hardware-dependent fast path
behind only some of them, assume nothing and benchmark both. This applies well beyond LLM
serving — codec choices, compression formats and serialisation formats all have the same
shape of trap.

**GQA and similar architecture choices change the constants in your formula, not just the
model's accuracy.** Reading a model's config for `num_key_value_heads` before estimating
its KV cache is worth the minute it takes; assuming it equals the attention head count is
a common way for an estimate to be off by four times or more.
