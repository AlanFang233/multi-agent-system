#!/usr/bin/env python3
"""Bounded, read-only HKGAI context-window probe.

Uses synthetic text only. Requires openai and python-dotenv.

Example:
  python hkgai_context_probe.py --levels 8,16,32,64,128
  python hkgai_context_probe.py --levels 256,512 --max-output-tokens 1024
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

WORDS = (
    "alpha", "cobalt", "stream", "orbit", "willow", "matrix", "harbor", "signal",
    "ledger", "meadow", "planet", "copper", "engine", "anchor", "violet", "horizon",
    "circuit", "puzzle", "valley", "winter", "garden", "rocket", "silver", "method",
    "kernel", "forest", "fabric", "cloud", "museum", "thunder", "beacon", "fossil",
    "magnet", "random", "bridge", "island", "dawn", "tensor", "paradox", "bamboo",
)


def make_prompt(target_k: int, chars_per_token: float) -> tuple[str, tuple[str, str, str]]:
    """Build varying synthetic input with markers at three depths.

    Target is approximate until the server reports actual prompt_tokens.
    """
    rng = random.Random(20261114 + target_k)
    markers = (
        f"START-{rng.randrange(10**9, 10**10)}",
        f"MIDDLE-{rng.randrange(10**9, 10**10)}",
        f"END-{rng.randrange(10**9, 10**10)}",
    )
    target_chars = int(target_k * 1000 * chars_per_token)
    chunks: list[str] = []
    length = 0
    i = 0
    while length < target_chars:
        i += 1
        line = f"record {i:07d}: " + " ".join(rng.choices(WORDS, k=16)) + "\n"
        chunks.append(line)
        length += len(line)
    haystack = "".join(chunks)
    middle = len(haystack) // 2
    start = (
        "下面是随机生成的测试文本，不包含真实用户数据。请忽略无关内容，" 
        "并在末尾回答指定字段。\n"
        f"START_KEY={markers[0]}\n"
    )
    text = (
        start + haystack[:middle] + f"\nMIDDLE_KEY={markers[1]}\n"
        + haystack[middle:] + f"\nEND_KEY={markers[2]}\n"
        + "请从上面的文本中提取 START_KEY、MIDDLE_KEY、END_KEY，" 
        "严格按顺序只输出三项对应的值，用英文逗号分隔。"
    )
    return text, markers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate effective HKGAI context with bounded calls")
    parser.add_argument("--levels", default="8,16,32,64", help="Approximate input sizes in thousands of tokens, e.g. 8,16,32,64,128")
    parser.add_argument("--chars-per-token", type=float, default=4.0, help="Initial characters-per-token guess (adapted after each successful call)")
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--report", type=Path, default=Path("context_probe_report.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.chars_per_token <= 0 or args.max_output_tokens < 1:
        raise SystemExit("Token estimates and output allowance must be positive")
    try:
        levels = [int(x.strip()) for x in args.levels.split(",")]
    except ValueError as exc:
        raise SystemExit("--levels must be comma-separated integer sizes in K") from exc
    if not levels or any(k <= 0 for k in levels) or levels != sorted(set(levels)):
        raise SystemExit("--levels must contain increasing unique positive integers")
    if levels[-1] > 1024:
        raise SystemExit("Safety cap: <=1024K per request. Change the source if you need a higher bound.")

    load_dotenv(dotenv_path=Path.cwd() / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY missing; set it in .env (do not share the key)")
    model_id = os.getenv("OPENAI_MODEL", "t2_hkgai-v3_fp8_1m_e7")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api1.hkgai.net/v1")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=base_url,
                    timeout=args.timeout, max_retries=0)

    print(f"Requested model: {model_id}")
    print(f"API endpoint: {base_url}")
    print(f"Testing levels (approx. K tokens): {levels}")
    print("Each call may consume billable input tokens; only synthetic text is sent.\n", flush=True)

    rows = []
    chars_per_token = args.chars_per_token
    for k in levels:
        text, markers = make_prompt(k, chars_per_token)
        print(f"[~{k}K] sending {len(text):,} characters...", flush=True)
        row: dict = {"target_k": k, "characters": len(text), "accepted": False}
        start = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": text}],
                max_tokens=args.max_output_tokens,
            )
            raw = response.model_dump()
            if raw.get("error"):
                row.update(error=raw["error"], error_type="response_error")
                print("  API returned an error object:", str(raw["error"])[:400])
            elif not response.choices:
                row.update(error="No choices in response", error_type="no_choices")
                print("  API returned no choices")
            else:
                usage = response.usage
                answer = response.choices[0].message.content or ""
                prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                row.update(
                    accepted=True,
                    response_model=response.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
                    finish_reason=response.choices[0].finish_reason,
                    recall_start=markers[0] in answer,
                    recall_middle=markers[1] in answer,
                    recall_end=markers[2] in answer,
                    answer_preview=answer[:200],
                )
                print(
                    "  ACCEPTED | prompt_tokens:", prompt_tokens,
                    "| response_model:", response.model,
                    "| finish_reason:", row["finish_reason"],
                    "| recall start/middle/end:",
                    row["recall_start"], row["recall_middle"], row["recall_end"],
                    flush=True,
                )
                if isinstance(prompt_tokens, int) and prompt_tokens > 0:
                    observed_ratio = len(text) / prompt_tokens
                    # Adjust estimated token density for subsequent requests.
                    chars_per_token = max(0.5, min(20.0, observed_ratio))
                if not answer:
                    print("  Note: empty answer can mean reasoning used all output tokens; acceptance still counts.")
        except Exception as exc:
            # Rate-limit, timeout, network, and generic 400s do NOT prove context overflow.
            row.update(error_type=type(exc).__name__, error=str(exc)[:500])
            print(f"  REQUEST FAILED: {row['error_type']}: {row['error']}")
        row["duration_seconds"] = round(time.monotonic() - start, 2)
        rows.append(row)
        if not row["accepted"]:
            print("  Stopping at first failure. Check error type before concluding context limit.")
            break

    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "requested_model": model_id,
        "base_url": base_url,
        "max_output_tokens": args.max_output_tokens,
        "observations": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    success = [r["prompt_tokens"] for r in rows if r.get("accepted") and isinstance(r.get("prompt_tokens"), int)]
    print(f"\nHighest confirmed accepted prompt: {max(success):,} tokens" if success else "\nNo successful prompt counts")
    print("A failed request gives an upper bound ONLY if the error explicitly reports a context limit.")
    print(f"Saved report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
