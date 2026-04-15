#!/usr/bin/env python3
"""
Gemini Batch API for Pass 2 GPT analysis.
Runs in parallel with OpenAI batches, using offset-based coordination.
"""

import json
import os
import time
from datetime import datetime

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
BATCH_FOLDER = os.path.join(os.path.dirname(__file__), "batch_files")
CASE_BRIEFS_PATH = os.path.join(os.path.dirname(__file__), "case_briefs.json")

MODEL = "gemini-3-flash-preview"  # or "gemini-2.5-pro"


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


def get_gemini_client():
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY") or _load_env_key("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found")
    return genai.Client(api_key=api_key)


def get_db_connection():
    import psycopg2
    host = os.environ.get("DB_HOST") or _load_env_key("DB_HOST")
    port = os.environ.get("DB_PORT") or _load_env_key("DB_PORT") or "5432"
    name = os.environ.get("DB_NAME") or _load_env_key("DB_NAME")
    user = os.environ.get("DB_USER") or _load_env_key("DB_USER")
    password = os.environ.get("DB_PASSWORD") or _load_env_key("DB_PASSWORD")
    return psycopg2.connect(host=host, port=port, dbname=name, user=user, password=password)


def load_case_briefs():
    if not os.path.exists(CASE_BRIEFS_PATH):
        return {}
    try:
        with open(CASE_BRIEFS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def build_prompt(instance, citation, case_brief, file_summary):
    """Build the analysis prompt - same as OpenAI version."""
    paragraph = instance.get("paragraph", "")[:3000]
    pinpoint = instance.get("pinpoint", "")
    pinpoint_mismatch = instance.get("pinpoint_validation", {}).get("missing", False)
    
    prompt = f"""Analyze this legal citation for accuracy and relevance.

DOCUMENT CONTEXT:
{file_summary[:1000] if file_summary else "Not available"}

CITATION: {citation}
PINPOINT: {pinpoint if pinpoint else "None specified"}
PINPOINT VALIDATION: {"MISMATCH - pinpoint not found in case" if pinpoint_mismatch else "OK or not checked"}

PARAGRAPH WHERE CITATION APPEARS:
{paragraph}

CASE BRIEF OF CITED CASE:
{case_brief[:4000] if case_brief else "Case brief not available"}

Please analyze:
1. RELATION_SCORE (1-5): How relevant is this citation to the paragraph's argument?
   1=Irrelevant, 2=Tangential, 3=Somewhat relevant, 4=Relevant, 5=Highly relevant
2. PINPOINT_SCORE (1-5): Does the pinpoint correctly reference the relevant part?
   1=Wrong, 2=Poor, 3=Acceptable, 4=Good, 5=Excellent (or N/A if no pinpoint)
3. REASON_CODE: One of [accurate, inaccurate, ambiguous, outdated, self_citation, out_of_jurisdiction, pinpoint_mismatch, age_mismatch]
4. BRIEF_EXPLANATION: 1-2 sentences explaining your assessment.

Respond in JSON format:
{{"relation_score": N, "pinpoint_score": N, "reason_code": "...", "explanation": "..."}}"""
    
    return prompt


def prepare_gemini_batch(max_requests=25, offset=0):
    """
    Prepare a batch for Gemini, using offset to avoid overlap with OpenAI batches.
    offset=0 means skip 0 instances, offset=25 means skip first 25, etc.
    """
    print("=" * 70)
    print("PREPARING GEMINI BATCH")
    print(f"  Max requests: {max_requests}, Offset: {offset}")
    print("=" * 70)
    
    os.makedirs(BATCH_FOLDER, exist_ok=True)
    
    case_briefs = load_case_briefs()
    print(f"Loaded {len(case_briefs)} case briefs")
    
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT filename, payload FROM file_outputs ORDER BY filename;")
        rows = cur.fetchall()
    conn.close()
    
    # Collect all instances needing Pass 2
    all_instances = []
    for filename, payload in rows:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except:
                continue
        if not isinstance(payload, dict):
            continue
        
        file_summary = payload.get("file_ai_summary", "")
        
        for cit_idx, item in enumerate(payload.get("results", [])):
            citation = item.get("citation", "")
            case_brief = case_briefs.get(citation, "")
            
            for inst_idx, inst in enumerate(item.get("instances", [])):
                # Skip if already being processed by another worker
                if inst.get("gpt_pass2_in_progress"):
                    continue
                if inst.get("gpt_needs_pass2"):
                    all_instances.append({
                        "filename": filename,
                        "citation_idx": cit_idx,
                        "instance_idx": inst_idx,
                        "citation": citation,
                        "instance": inst,
                        "case_brief": case_brief,
                        "file_summary": file_summary
                    })
    
    print(f"Total instances needing Pass 2: {len(all_instances)}")
    
    # Apply offset and limit
    instances_to_process = all_instances[offset:offset + max_requests]
    
    if not instances_to_process:
        print(f"\n✓ No instances at offset {offset}!")
        return None, None
    
    print(f"Processing instances {offset} to {offset + len(instances_to_process)}")
    
    # Build Gemini inline requests
    inline_requests = []
    request_mapping = {}
    
    for i, item in enumerate(instances_to_process):
        custom_id = f"gemini_{item['filename']}_{item['citation_idx']}_{item['instance_idx']}"
        
        prompt = build_prompt(
            item["instance"],
            item["citation"],
            item["case_brief"],
            item["file_summary"]
        )
        
        inline_requests.append({
            'contents': [{
                'parts': [{'text': prompt}],
                'role': 'user'
            }]
        })
        
        request_mapping[f"request-{i}"] = {
            "filename": item["filename"],
            "citation_idx": item["citation_idx"],
            "instance_idx": item["instance_idx"],
            "citation": item["citation"]
        }
    
    # Save mapping file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mapping_file_path = os.path.join(BATCH_FOLDER, f"gemini_mapping_{timestamp}.json")
    with open(mapping_file_path, "w", encoding="utf-8") as f:
        json.dump(request_mapping, f, indent=2)
    
    print(f"\n✓ Prepared {len(inline_requests)} requests for Gemini")
    print(f"✓ Mapping saved: {mapping_file_path}")
    
    # Mark selected instances as "in progress" in database
    print("Marking instances as in_progress...")
    conn = get_db_connection()
    if conn:
        # Group by filename
        updates_by_file = {}
        for key, mapping in request_mapping.items():
            filename = mapping["filename"]
            if filename not in updates_by_file:
                updates_by_file[filename] = []
            updates_by_file[filename].append((mapping["citation_idx"], mapping["instance_idx"]))
        
        with conn.cursor() as cur:
            for filename, indices in updates_by_file.items():
                cur.execute("SELECT payload FROM file_outputs WHERE filename = %s;", (filename,))
                row = cur.fetchone()
                if not row:
                    continue
                payload = row[0]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                
                for cit_idx, inst_idx in indices:
                    if cit_idx < len(payload.get("results", [])):
                        insts = payload["results"][cit_idx].get("instances", [])
                        if inst_idx < len(insts):
                            insts[inst_idx]["gpt_pass2_in_progress"] = timestamp
                
                cur.execute(
                    "UPDATE file_outputs SET payload = %s WHERE filename = %s;",
                    (json.dumps(payload), filename)
                )
            conn.commit()
        conn.close()
        print(f"✓ Marked {len(request_mapping)} instances as in_progress")
    
    return inline_requests, mapping_file_path


def submit_gemini_batch(inline_requests, mapping_file_path):
    """Submit inline requests to Gemini Batch API."""
    print("\n" + "=" * 70)
    print("SUBMITTING GEMINI BATCH")
    print("=" * 70)
    
    client = get_gemini_client()
    
    print(f"Creating batch job with {len(inline_requests)} requests...")
    
    batch_job = client.batches.create(
        model=f"models/{MODEL}",
        src=inline_requests,
        config={
            'display_name': f"citation-analysis-{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        },
    )
    
    print(f"✓ Batch created: {batch_job.name}")
    
    # Save batch info
    batch_info = {
        "batch_name": batch_job.name,
        "mapping_file": mapping_file_path,
        "created_at": datetime.now().isoformat(),
        "request_count": len(inline_requests)
    }
    
    info_file = mapping_file_path.replace("_mapping_", "_info_")
    with open(info_file, "w") as f:
        json.dump(batch_info, f, indent=2)
    
    print(f"✓ Batch info saved: {info_file}")
    
    return batch_job.name


def check_gemini_status(batch_name):
    """Check status of a Gemini batch job."""
    print("=" * 70)
    print(f"CHECKING GEMINI BATCH: {batch_name}")
    print("=" * 70)
    
    client = get_gemini_client()
    batch_job = client.batches.get(name=batch_name)
    
    print(f"\nStatus: {batch_job.state.name}")
    
    return batch_job


def retrieve_gemini_results(batch_name, mapping_file_path):
    """Retrieve results from completed Gemini batch and update database."""
    print("=" * 70)
    print(f"RETRIEVING GEMINI RESULTS: {batch_name}")
    print("=" * 70)
    
    client = get_gemini_client()
    batch_job = client.batches.get(name=batch_name)
    
    if batch_job.state.name != 'JOB_STATE_SUCCEEDED':
        print(f"Batch not complete. Status: {batch_job.state.name}")
        return False
    
    # Load mapping
    with open(mapping_file_path, "r") as f:
        request_mapping = json.load(f)
    
    # Get inline responses
    if not batch_job.dest or not batch_job.dest.inlined_responses:
        print("No inline responses found")
        return False
    
    responses = batch_job.dest.inlined_responses
    print(f"Retrieved {len(responses)} responses")
    
    # Process results
    results = {}
    for i, inline_response in enumerate(responses):
        key = f"request-{i}"
        if key not in request_mapping:
            continue
        
        mapping = request_mapping[key]
        filename = mapping["filename"]
        
        if filename not in results:
            results[filename] = []
        
        parsed = None
        if inline_response.response:
            try:
                text = inline_response.response.text
                # Try to parse JSON from response
                import re
                json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
            except:
                pass
        
        results[filename].append({
            "citation_idx": mapping["citation_idx"],
            "instance_idx": mapping["instance_idx"],
            "parsed": parsed,
            "error": str(inline_response.error) if inline_response.error else None
        })
    
    # Update database
    conn = get_db_connection()
    updated = 0
    
    for filename, file_results in results.items():
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM file_outputs WHERE filename = %s;", (filename,))
            row = cur.fetchone()
            if not row:
                continue
            
            payload = row[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            
            for result in file_results:
                cit_idx = result["citation_idx"]
                inst_idx = result["instance_idx"]
                parsed = result["parsed"]
                
                if parsed and cit_idx < len(payload.get("results", [])):
                    instances = payload["results"][cit_idx].get("instances", [])
                    if inst_idx < len(instances):
                        inst = instances[inst_idx]
                        inst["gpt_relation_score"] = parsed.get("relation_score")
                        inst["gpt_pinpoint_score"] = parsed.get("pinpoint_score")
                        inst["gpt_reason_code"] = parsed.get("reason_code")
                        inst["gpt_explanation"] = parsed.get("explanation")
                        inst["gpt_pass"] = 2  # Mark as Pass 2 completed
                        
                        # Remove the needs_pass2 and in_progress flags
                        if "gpt_needs_pass2" in inst:
                            del inst["gpt_needs_pass2"]
                        if "gpt_pass2_in_progress" in inst:
                            del inst["gpt_pass2_in_progress"]
                        
                        updated += 1
            
            cur.execute(
                "UPDATE file_outputs SET payload = %s WHERE filename = %s;",
                (json.dumps(payload), filename)
            )
        conn.commit()
    
    conn.close()
    print(f"✓ Updated {updated} instances in database")
    return True


def list_gemini_batches():
    """List all Gemini batch jobs."""
    client = get_gemini_client()
    batch_jobs = client.batches.list()
    
    print("=" * 70)
    print("GEMINI BATCH JOBS")
    print("=" * 70)
    
    for batch in batch_jobs:
        print(f"\n{batch.name}")
        print(f"  Status: {batch.state.name}")
        if hasattr(batch, 'display_name'):
            print(f"  Name: {batch.display_name}")


def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 batch_gemini.py prepare [max_requests] [offset]")
        print("  python3 batch_gemini.py submit [max_requests] [offset]")
        print("  python3 batch_gemini.py status <batch_name>")
        print("  python3 batch_gemini.py retrieve <batch_name> <mapping_file>")
        print("  python3 batch_gemini.py list")
        return
    
    command = sys.argv[1].lower()
    
    if command == "prepare":
        max_req = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        offset = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        prepare_gemini_batch(max_req, offset)
    
    elif command == "submit":
        max_req = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        offset = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        inline_requests, mapping_file = prepare_gemini_batch(max_req, offset)
        if inline_requests:
            batch_name = submit_gemini_batch(inline_requests, mapping_file)
            print(f"\nTo check status: python3 batch_gemini.py status {batch_name}")
    
    elif command == "status":
        if len(sys.argv) < 3:
            print("Usage: python3 batch_gemini.py status <batch_name>")
            return
        check_gemini_status(sys.argv[2])
    
    elif command == "retrieve":
        if len(sys.argv) < 4:
            print("Usage: python3 batch_gemini.py retrieve <batch_name> <mapping_file>")
            return
        retrieve_gemini_results(sys.argv[2], sys.argv[3])
    
    elif command == "list":
        list_gemini_batches()
    
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
