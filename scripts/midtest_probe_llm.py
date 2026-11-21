"""Mid_Test connectivity probe: ONE minimal real-LLM request.

Reads credentials from .env.hkgai.bak WITHOUT printing them. Bounded:
timeout 90s, max_retries=0, max_tokens=10. Prints only latency/model/usage
and a short content prefix.
"""
import time
import sys


def load_env(path: str) -> dict:
    env = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main() -> int:
    env = load_env(".env.hkgai.bak")
    from openai import OpenAI
    client = OpenAI(api_key=env["OPENAI_API_KEY"],
                    base_url=env["OPENAI_BASE_URL"],
                    timeout=90.0, max_retries=0)
    t0 = time.time()
    try:
        r = client.chat.completions.create(
            model=env["OPENAI_MODEL"],
            messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
            max_tokens=10,
        )
    except Exception as exc:
        print(f"PROBE: FAIL {type(exc).__name__}: {str(exc)[:200]}")
        return 1
    dt = time.time() - t0
    print("latency_s:", round(dt, 1))
    print("model:", r.model)
    print("content:", repr((r.choices[0].message.content or "")[:80]))
    print("usage:", r.usage)
    print("PROBE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
