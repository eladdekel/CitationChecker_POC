#!/usr/bin/env python3
"""
Generate case briefs from full case texts using gpt-5-mini.
This is a one-time operation that creates summaries for all cases,
which can then be used for cheaper Pass 2 analysis.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
CASE_TEXTS_PATH = os.path.join(os.path.dirname(__file__), "case_texts.json")
CASE_BRIEFS_PATH = os.path.join(os.path.dirname(__file__), "case_briefs.json")

MODEL = "gpt-5-mini"


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


def get_openai_client():
    api_key = os.environ.get("OPENAI_API_KEY") or _load_env_key("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found in environment or .env file")
    return OpenAI(api_key=api_key)


def load_case_texts():
    if not os.path.exists(CASE_TEXTS_PATH):
        return {}
    try:
        with open(CASE_TEXTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        case_map = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    citation = item.get("citation")
                    text = item.get("case_text")
                    if citation and text:
                        case_map[citation] = text
        return case_map
    except Exception:
        return {}


def load_existing_briefs():
    if not os.path.exists(CASE_BRIEFS_PATH):
        return {}
    try:
        with open(CASE_BRIEFS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_briefs(briefs):
    with open(CASE_BRIEFS_PATH, "w", encoding="utf-8") as f:
        json.dump(briefs, f, ensure_ascii=False, indent=2)


def generate_brief(client, citation, case_text, max_retries=3):
    """Generate a case brief from the full case text."""
    prompt = f"""Summarize this legal case into a concise case brief (max 800 words).

Include:
1. Case citation and court
2. Key facts (2-3 sentences)
3. Legal issue(s)
4. Holding/Decision
5. Key legal principles or ratio decidendi
6. Any notable precedents cited

Case Citation: {citation}

Case Text:
{case_text[:50000]}  

Provide only the case brief, no other commentary."""

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=5000
            )
            content = response.choices[0].message.content
            if content:
                return content.strip()
            else:
                # Empty response - retry after delay
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                finish_reason = response.choices[0].finish_reason
                refusal = getattr(response.choices[0].message, 'refusal', None)
                return f"ERROR: Empty response after {max_retries} attempts (finish_reason={finish_reason}, refusal={refusal})"
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            return f"ERROR: {str(e)}"
    
    return "ERROR: Max retries exceeded"


def main():
    print("=" * 70)
    print("CASE BRIEF GENERATION")
    print("=" * 70)
    
    # Load case texts
    case_texts = load_case_texts()
    print(f"Loaded {len(case_texts)} case texts")
    
    # Load existing briefs (to skip ones already done)
    existing_briefs = load_existing_briefs()
    print(f"Found {len(existing_briefs)} existing briefs")
    
    # Count and remove empty/error briefs for re-processing
    empty_briefs = [k for k, v in existing_briefs.items() if not v or len(v) < 50 or v.startswith("ERROR:")]
    if empty_briefs:
        print(f"  (Removing {len(empty_briefs)} empty/failed briefs for re-processing)")
        for k in empty_briefs:
            del existing_briefs[k]
    
    # Identify cases needing briefs
    cases_to_process = []
    for citation, text in case_texts.items():
        if citation not in existing_briefs:
            cases_to_process.append((citation, text))
    
    print(f"Cases needing briefs: {len(cases_to_process)}")
    
    if not cases_to_process:
        print("\n✓ All cases already have briefs!")
        return
    
    # Estimate cost
    total_input_chars = sum(len(text) for _, text in cases_to_process)
    estimated_input_tokens = total_input_chars // 4
    estimated_output_tokens = len(cases_to_process) * 800  # ~800 tokens per brief
    
    # Using gpt-5-mini pricing (assumed similar to gpt-4o-mini: $0.15/1M in, $0.60/1M out)
    input_cost = (estimated_input_tokens / 1_000_000) * 0.15
    output_cost = (estimated_output_tokens / 1_000_000) * 0.60
    total_cost = input_cost + output_cost
    
    print(f"\nEstimated cost:")
    print(f"  Input:  ~{estimated_input_tokens:,} tokens = ${input_cost:.2f}")
    print(f"  Output: ~{estimated_output_tokens:,} tokens = ${output_cost:.2f}")
    print(f"  Total:  ${total_cost:.2f}")
    
    # Confirm
    response = input(f"\nProceed with generating {len(cases_to_process)} briefs? (y/n): ")
    if response.lower() != 'y':
        print("Cancelled.")
        return
    
    # Generate briefs with parallel workers
    client = get_openai_client()
    briefs = existing_briefs.copy()
    
    # Thread-safe lock for saving
    import threading
    briefs_lock = threading.Lock()
    
    MAX_WORKERS = 1  # Reduced to avoid API throttling
    
    print(f"\nGenerating briefs with {MAX_WORKERS} parallel workers...")
    completed = 0
    errors = 0
    
    def process_brief(args):
        idx, citation, text = args
        try:
            # Small delay to avoid overwhelming the API
            time.sleep(0.5)
            brief = generate_brief(client, citation, text)
            return idx, citation, brief, None
        except Exception as e:
            return idx, citation, None, str(e)
    
    # Prepare work items with index
    work_items = [(i, citation, text) for i, (citation, text) in enumerate(cases_to_process)]
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_brief, item): item for item in work_items}
        
        for future in as_completed(futures):
            idx, citation, brief, error = future.result()
            
            if error:
                errors += 1
                print(f"  ✗ [{idx+1}/{len(cases_to_process)}] {citation}: {error}")
            elif brief and brief.startswith("ERROR:"):
                errors += 1
                print(f"  ✗ [{idx+1}/{len(cases_to_process)}] {citation}: {brief}")
            else:
                with briefs_lock:
                    briefs[citation] = brief
                    completed += 1
                    # Save after each successful brief
                    save_briefs(briefs)
                print(f"  ✓ [{idx+1}/{len(cases_to_process)}] {citation} ({len(brief)} chars)")
    
    print(f"\n" + "=" * 70)
    print(f"COMPLETE")
    print(f"=" * 70)
    print(f"  Briefs generated: {completed}")
    print(f"  Errors: {errors}")
    print(f"  Total briefs: {len(briefs)}")
    print(f"  Saved to: {CASE_BRIEFS_PATH}")


if __name__ == "__main__":
    main()
