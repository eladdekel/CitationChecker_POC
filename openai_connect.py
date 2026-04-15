import os
import time
import threading

from openai import OpenAI, APIError, RateLimitError, BadRequestError


ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_TS = 0.0
_CLIENT_LOCK = threading.Lock()
_CLIENT = None


def _load_env_key(key_name, env_path=ENV_PATH):
    if not os.path.exists(env_path):
        return ""
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                k, v = line.split("=", 1)
                if k.strip() == key_name:
                    return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _get_client():
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            api_key = os.environ.get("OPENAI_API_KEY") or _load_env_key("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not found in environment or .env file")
            _CLIENT = OpenAI(api_key=api_key)
        return _CLIENT


def run_prompt(prompt, model=None, timeout_seconds=60):
    """
    Send a prompt to OpenAI Chat Completions and return the response text.

    Required:
      - OPENAI_API_KEY (env or .env)
    Optional:
      - OPENAI_MODEL          (default: gpt-5-nano)
      - OPENAI_MIN_INTERVAL   (seconds between requests; default: 0)
      - OPENAI_MAX_RETRIES    (default: 5)
    """
    if model is None:
        model = (
            os.environ.get("OPENAI_MODEL")
            or _load_env_key("OPENAI_MODEL")
            or "gpt-5-nano"
        )

    min_interval = float(
        os.environ.get("OPENAI_MIN_INTERVAL")
        or _load_env_key("OPENAI_MIN_INTERVAL")
        or "0"
    )
    max_retries = int(
        os.environ.get("OPENAI_MAX_RETRIES")
        or _load_env_key("OPENAI_MAX_RETRIES")
        or "5"
    )

    client = _get_client()

    for attempt in range(max_retries + 1):
        # Simple global rate limiter between requests
        if min_interval > 0:
            with _RATE_LOCK:
                global _LAST_REQUEST_TS
                now = time.time()
                wait = _LAST_REQUEST_TS + min_interval - now
                if wait > 0:
                    time.sleep(wait)
                _LAST_REQUEST_TS = time.time()

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_seconds,
            )
            return response.choices[0].message.content
        except RateLimitError:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise
        except BadRequestError as e:
            # Don't retry obvious client-side errors (oversized context, bad input)
            if "context_length_exceeded" in str(e):
                raise RuntimeError("context_length_exceeded — reduce input size") from e
            raise
        except APIError:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise
