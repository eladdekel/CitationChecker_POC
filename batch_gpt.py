#!/usr/bin/env python3
"""
Batch API mode for GPT citation analysis.
Uses OpenAI's Batch API for 50% cost savings.
"""

import json
import os
import re
import time
from datetime import datetime
from openai import OpenAI

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
OUTPUT_JSONS_FOLDER = os.path.join(os.path.dirname(__file__), "output_jsons")
CASE_TEXTS_PATH = os.path.join(os.path.dirname(__file__), "case_texts.json")
CASE_BRIEFS_PATH = os.path.join(os.path.dirname(__file__), "case_briefs.json")
BATCH_FOLDER = os.path.join(os.path.dirname(__file__), "batch_files")

MODEL = "gpt-5.2"


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


def get_db_conn():
    try:
        import psycopg2
    except ImportError:
        return None

    host = os.environ.get("DB_HOST") or _load_env_key("DB_HOST")
    port = os.environ.get("DB_PORT") or _load_env_key("DB_PORT") or "5432"
    name = os.environ.get("DB_NAME") or _load_env_key("DB_NAME")
    user = os.environ.get("DB_USER") or _load_env_key("DB_USER")
    password = os.environ.get("DB_PASSWORD") or _load_env_key("DB_PASSWORD")

    if not all([host, name, user, password]):
        return None

    return psycopg2.connect(host=host, port=port, dbname=name, user=user, password=password)


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
                    if citation:
                        case_map[citation] = text or ""
        return case_map
    except Exception:
        return {}


def load_case_briefs():
    """Load pre-generated case briefs for cheaper Pass 2 analysis."""
    if not os.path.exists(CASE_BRIEFS_PATH):
        return {}
    try:
        with open(CASE_BRIEFS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _overlap_score(text_a, text_b):
    def tokenize(text):
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    set_a = tokenize(text_a)
    set_b = tokenize(text_b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / max(1, len(set_a | set_b))


def build_prompt(citation, paragraph, pinpoints, file_summary, keywords, topics,
                 canlii_title, canlii_date, hf_name, hf_date, metadata_flags,
                 title_overlap, keyword_overlap, pinpoint_mismatch,
                 out_of_jurisdiction, age_mismatch_flag, self_citation_by_docket,
                 case_text=None):
    """Build the analysis prompt."""
    context_block = (
        "Document summary:\n"
        f"{file_summary}\n\n"
        "Citation paragraph:\n"
        f"{paragraph}\n\n"
        "Pinpoints extracted (if any):\n"
        f"{pinpoints}\n\n"
        "Citation metadata (keywords/topics):\n"
        f"keywords: {keywords}\n"
        f"topics: {topics}\n\n"
        "Citation metadata (titles/dates):\n"
        f"canlii_title: {canlii_title}\n"
        f"canlii_date: {canlii_date}\n"
        f"hf_name: {hf_name}\n"
        f"hf_date: {hf_date}\n"
        f"metadata_flags: {metadata_flags}\n"
        f"title_overlap: {title_overlap}\n"
        f"keyword_overlap: {keyword_overlap}\n"
        f"pinpoint_mismatch: {pinpoint_mismatch}\n"
        f"out_of_jurisdiction: {out_of_jurisdiction}\n"
        f"age_mismatch: {age_mismatch_flag}\n"
        f"self_citation_docket: {self_citation_by_docket}\n\n"
    )

    if case_text:
        context_block += f"Citation text:\n{case_text}\n\n"

    prompt = (
        "You are evaluating whether a legal citation is used correctly.\n"
        "Return ONLY a JSON object with keys:\n"
        "relation_score (0-1), pinpoint_score (0-1), relation_reasoning (string), "
        "reason_code (string), is_self_citation (true/false).\n"
        "reason_code must be one of: keyword_mismatch, topic_mismatch, title_mismatch, "
        "year_mismatch, pinpoint_mismatch, out_of_jurisdiction, age_mismatch, "
        "self_citation, self_citation_docket, missing_metadata, low_overlap, below_threshold, other.\n\n"
        f"{context_block}"
        "Task:\n"
        "1) relation_score: how well the citation matches the document's use.\n"
        "2) pinpoint_score: whether the pinpoint usage is correct.\n"
        "3) relation_reasoning: brief justification for relation_score.\n"
        "4) reason_code: pick the best single code.\n"
        "5) is_self_citation: true if the citation refers to this document itself.\n"
    )
    return prompt


def prepare_batch_file(max_requests=None, offset=0):
    """
    Prepare a JSONL batch file for Pass 2 analysis only.
    Only includes instances that have gpt_needs_pass2=True (flagged from Pass 1).
    Uses case briefs (if available) instead of full case text for cost savings.
    
    Args:
        max_requests: Optional limit on number of requests to include (for token limits)
        offset: Number of instances to skip (for coordinating with parallel workers)
    
    Returns the path to the batch file and a mapping file.
    """
    print("=" * 70)
    print("PREPARING BATCH FILE FOR PASS 2 GPT ANALYSIS")
    if max_requests:
        print(f"  (Limited to {max_requests} requests, offset: {offset})")
    print("=" * 70)

    os.makedirs(BATCH_FOLDER, exist_ok=True)
    
    # Load case briefs (preferred - cheaper) and case texts (fallback)
    case_briefs = load_case_briefs()
    case_texts = load_case_texts()
    print(f"Loaded {len(case_briefs)} case briefs (preferred)")
    print(f"Loaded {len(case_texts)} case texts (fallback)")
    
    if not case_briefs:
        print("\n⚠️  WARNING: No case briefs found!")
        print("   Run 'python3 generate_briefs.py' first for 80% cost savings.")
        response = input("   Continue with full case texts? (y/n): ")
        if response.lower() != 'y':
            return None, None

    # Get data from DB
    conn = get_db_conn()
    if not conn:
        print("Could not connect to database")
        return None, None

    with conn.cursor() as cur:
        cur.execute("SELECT filename, payload FROM file_outputs ORDER BY filename;")
        rows = cur.fetchall()
    conn.close()


    print(f"Found {len(rows)} files in database")

    # First, collect ALL instances that need Pass 2 (to enable offset-based coordination)
    all_instances = []
    for filename, payload in rows:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except:
                continue
        if not isinstance(payload, dict):
            continue

        file_summary = payload.get("file_ai_summary") or payload.get("FILE_AI_SUMMARY", "")
        doc_court_no = payload.get("court_no", "")

        for citation_idx, item in enumerate(payload.get("results", [])):
            citation = (item.get("citation") or "").strip()
            if not citation:
                continue

            canlii_resp = item.get("canlii_api_response") or {}
            keywords = canlii_resp.get("keywords", "")
            topics = canlii_resp.get("topics", "")
            canlii_title = canlii_resp.get("title", "")
            canlii_date = canlii_resp.get("decisionDate", "")
            hf_result = item.get("hf_result") or {}
            hf_name = hf_result.get("name_en", "")
            hf_date = hf_result.get("document_date_en", "")
            
            # Prefer case brief over full case text (much cheaper)
            case_brief = case_briefs.get(citation, "")
            case_text = case_texts.get(citation, "") if not case_brief else ""
            case_content = case_brief or case_text  # Use brief if available

            for inst_idx, inst in enumerate(item.get("instances", [])):
                # ONLY process instances that need Pass 2 (flagged from Pass 1)
                if not inst.get("gpt_needs_pass2"):
                    continue
                
                # Skip if already being processed by another worker
                if inst.get("gpt_pass2_in_progress"):
                    continue
                
                # Skip if no case content available (Pass 2 requires brief or text)
                if not case_content:
                    continue
                
                paragraph = (inst.get("paragraph") or "").strip()
                if not paragraph:
                    continue
                
                all_instances.append({
                    "filename": filename,
                    "citation_idx": citation_idx,
                    "instance_idx": inst_idx,
                    "citation": citation,
                    "instance": inst,
                    "file_summary": file_summary,
                    "keywords": keywords,
                    "topics": topics,
                    "canlii_title": canlii_title,
                    "canlii_date": canlii_date,
                    "hf_name": hf_name,
                    "hf_date": hf_date,
                    "case_content": case_content,
                    "is_brief": bool(case_brief),
                    "paragraph": paragraph,
                    "pinpoints": inst.get("pinpoints", []),
                })

    print(f"\n  Total instances needing Pass 2: {len(all_instances)}")
    
    # Apply offset and limit
    if max_requests:
        instances_to_process = all_instances[offset:offset + max_requests]
    else:
        instances_to_process = all_instances[offset:]
    
    if not instances_to_process:
        print(f"\n✓ No instances at offset {offset}!")
        return None, None
    
    print(f"  Processing instances {offset} to {offset + len(instances_to_process)}")

    # Prepare batch requests
    batch_requests = []
    request_mapping = {}  # Maps custom_id to (filename, citation_index, instance_index)
    used_briefs = 0
    used_full_text = 0

    for item in instances_to_process:
        # Calculate overlap
        keyword_overlap = _overlap_score(
            item["paragraph"], 
            f"{item['keywords']} {item['topics']} {item['canlii_title']} {item['hf_name']}"
        )

        # Build custom_id for tracking
        custom_id = f"req_{item['filename']}_{item['citation_idx']}_{item['instance_idx']}"
        
        # Build the prompt WITH case brief/text (this is Pass 2)
        prompt = build_prompt(
            citation=item["citation"],
            paragraph=item["paragraph"],
            pinpoints=item["pinpoints"],
            file_summary=item["file_summary"],
            keywords=item["keywords"],
            topics=item["topics"],
            canlii_title=item["canlii_title"],
            canlii_date=item["canlii_date"],
            hf_name=item["hf_name"],
            hf_date=item["hf_date"],
            metadata_flags=[],
            title_overlap=0.0,
            keyword_overlap=keyword_overlap,
            pinpoint_mismatch=item["instance"].get("pinpoint_validation", {}).get("missing", False),
            out_of_jurisdiction=item["instance"].get("out_of_jurisdiction_flag", False),
            age_mismatch_flag=item["instance"].get("age_mismatch_flag", False),
            self_citation_by_docket=item["instance"].get("self_citation_docket_flag", False),
            case_text=item["case_content"]  # Use brief (preferred) or full text
        )
        
        # Track source
        if item["is_brief"]:
            used_briefs += 1
        else:
            used_full_text += 1

        # Create batch request
        batch_request = {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_completion_tokens": 500,
                "temperature": 1
            }
        }
        
        batch_requests.append(batch_request)
        request_mapping[custom_id] = {
            "filename": item["filename"],
            "citation_idx": item["citation_idx"],
            "instance_idx": item["instance_idx"],
            "citation": item["citation"]
        }

    print(f"\n  Using case briefs:        {used_briefs} (cheaper)")
    print(f"  Using full case text:     {used_full_text}")
    print(f"  Requests to batch:        {len(batch_requests)}")

    if not batch_requests:
        print("\n✓ No instances need Pass 2 batch processing!")
        return None, None

    # Write batch file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_file_path = os.path.join(BATCH_FOLDER, f"batch_input_{timestamp}.jsonl")
    mapping_file_path = os.path.join(BATCH_FOLDER, f"batch_mapping_{timestamp}.json")

    with open(batch_file_path, "w", encoding="utf-8") as f:
        for req in batch_requests:
            f.write(json.dumps(req) + "\n")

    with open(mapping_file_path, "w", encoding="utf-8") as f:
        json.dump(request_mapping, f, indent=2)

    file_size_mb = os.path.getsize(batch_file_path) / (1024 * 1024)
    
    print(f"\n✓ Created batch file: {batch_file_path}")
    print(f"  - Requests: {len(batch_requests)}")
    print(f"  - File size: {file_size_mb:.2f} MB")
    print(f"✓ Created mapping file: {mapping_file_path}")
    
    if file_size_mb > 200:
        print(f"\n⚠️  WARNING: File size exceeds 200MB limit!")
        print(f"   Consider splitting the batch or reducing case text size.")
    
    # Mark selected instances as "in progress" in database
    print("Marking instances as in_progress...")
    conn = get_db_conn()
    if conn:
        # Group by filename
        updates_by_file = {}
        for custom_id, mapping in request_mapping.items():
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
    
    return batch_file_path, mapping_file_path


def submit_batch(batch_file_path):
    """Upload the batch file and create a batch job."""
    MAX_BATCH_SIZE_MB = 200  # OpenAI limit for gpt-5.2 model
    
    print("\n" + "=" * 70)
    print("SUBMITTING BATCH TO OPENAI")
    print("=" * 70)
    
    # Check file size before uploading
    file_size_bytes = os.path.getsize(batch_file_path)
    file_size_mb = file_size_bytes / (1024 * 1024)
    
    print(f"\nBatch file: {batch_file_path}")
    print(f"File size:  {file_size_mb:.2f} MB")
    
    if file_size_mb > MAX_BATCH_SIZE_MB:
        print(f"\n❌ ERROR: Batch file exceeds {MAX_BATCH_SIZE_MB}MB limit for gpt-5.2 model!")
        print(f"   Current size: {file_size_mb:.2f} MB")
        print(f"   Maximum size: {MAX_BATCH_SIZE_MB} MB")
        print(f"\n   Suggestions:")
        print(f"   1. Split the batch into smaller chunks")
        print(f"   2. Reduce case text length (summarize first)")
        print(f"   3. Process in multiple batch jobs")
        return None

    client = get_openai_client()

    # Upload the file
    print("\nUploading batch file...")
    with open(batch_file_path, "rb") as f:
        file_response = client.files.create(file=f, purpose="batch")
    
    print(f"✓ File uploaded: {file_response.id}")

    # Create the batch
    print("Creating batch job...")
    batch = client.batches.create(
        input_file_id=file_response.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={
            "description": "Citation GPT analysis batch",
            "created_at": datetime.now().isoformat()
        }
    )

    print(f"✓ Batch created: {batch.id}")
    print(f"  - Status: {batch.status}")
    print(f"  - Endpoint: {batch.endpoint}")

    # Save batch info
    batch_info_path = batch_file_path.replace("_input_", "_info_").replace(".jsonl", ".json")
    batch_info = {
        "batch_id": batch.id,
        "input_file_id": file_response.id,
        "status": batch.status,
        "created_at": datetime.now().isoformat(),
        "batch_file_path": batch_file_path,
        "mapping_file_path": batch_file_path.replace("_input_", "_mapping_").replace(".jsonl", ".json")
    }
    with open(batch_info_path, "w") as f:
        json.dump(batch_info, f, indent=2)
    
    print(f"✓ Batch info saved: {batch_info_path}")
    print(f"\nTo check status, run: python3 batch_gpt.py status {batch.id}")
    
    return batch.id


def check_status(batch_id):
    """Check the status of a batch job."""
    print("=" * 70)
    print(f"CHECKING BATCH STATUS: {batch_id}")
    print("=" * 70)

    client = get_openai_client()
    batch = client.batches.retrieve(batch_id)

    print(f"\nStatus: {batch.status}")
    print(f"Created: {datetime.fromtimestamp(batch.created_at).isoformat()}")
    
    if batch.in_progress_at:
        print(f"Started: {datetime.fromtimestamp(batch.in_progress_at).isoformat()}")
    
    if batch.request_counts:
        total = batch.request_counts.total
        completed = batch.request_counts.completed
        failed = batch.request_counts.failed
        print(f"\nProgress: {completed}/{total} completed ({failed} failed)")
        if total > 0:
            pct = (completed / total) * 100
            print(f"          {pct:.1f}% complete")

    if batch.status == "completed":
        print(f"\n✓ Batch completed!")
        print(f"  Output file: {batch.output_file_id}")
        if batch.error_file_id:
            print(f"  Error file: {batch.error_file_id}")
        print(f"\nTo retrieve results, run: python3 batch_gpt.py retrieve {batch_id}")
    elif batch.status == "failed":
        print(f"\n✗ Batch failed!")
        if batch.errors:
            for error in batch.errors.data:
                print(f"  Error: {error.message}")
    elif batch.status == "expired":
        print(f"\n✗ Batch expired before completion")
        if batch.output_file_id:
            print(f"  Partial results available: {batch.output_file_id}")

    return batch


def retrieve_results(batch_id, mapping_file=None):
    """Retrieve and process batch results."""
    print("=" * 70)
    print(f"RETRIEVING BATCH RESULTS: {batch_id}")
    print("=" * 70)

    client = get_openai_client()
    batch = client.batches.retrieve(batch_id)

    if batch.status != "completed":
        print(f"Batch is not complete. Current status: {batch.status}")
        return None

    if not batch.output_file_id:
        print("No output file available")
        return None

    # Download results
    print("Downloading results...")
    file_response = client.files.content(batch.output_file_id)
    results_text = file_response.text

    # Parse results
    results = []
    for line in results_text.strip().split("\n"):
        if line:
            results.append(json.loads(line))

    print(f"✓ Retrieved {len(results)} results")

    # Save raw results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = os.path.join(BATCH_FOLDER, f"batch_results_{timestamp}.jsonl")
    with open(results_file, "w") as f:
        f.write(results_text)
    print(f"✓ Saved raw results: {results_file}")

    # Find the correct mapping file
    if not mapping_file:
        # First, try to find it from the batch_info file
        for fname in sorted(os.listdir(BATCH_FOLDER), reverse=True):
            if fname.startswith("batch_info_") and fname.endswith(".json"):
                info_path = os.path.join(BATCH_FOLDER, fname)
                try:
                    with open(info_path, "r") as f:
                        info = json.load(f)
                    if info.get("batch_id") == batch_id:
                        mapping_file = info.get("mapping_file_path")
                        print(f"Found mapping from batch_info: {mapping_file}")
                        break
                except:
                    continue
        
        # Fallback: use the most recent mapping file
        if not mapping_file:
            mapping_files = sorted([
                f for f in os.listdir(BATCH_FOLDER) 
                if f.startswith("batch_mapping_") and f.endswith(".json")
            ], reverse=True)
            if mapping_files:
                mapping_file = os.path.join(BATCH_FOLDER, mapping_files[0])
                print(f"Using most recent mapping file: {mapping_file}")

    if not mapping_file or not os.path.exists(mapping_file):
        print("Warning: Could not find mapping file")
        return results

    with open(mapping_file, "r") as f:
        request_mapping = json.load(f)

    # Process results and update database
    print("\nProcessing results and updating database...")
    conn = get_db_conn()
    if not conn:
        print("Could not connect to database")
        return results

    # Load all payloads
    with conn.cursor() as cur:
        cur.execute("SELECT filename, payload FROM file_outputs;")
        rows = cur.fetchall()
    
    payloads = {}
    for filename, payload in rows:
        if isinstance(payload, str):
            payload = json.loads(payload)
        payloads[filename] = payload

    # Update payloads with results
    updates_made = 0
    errors = 0
    
    for result in results:
        custom_id = result.get("custom_id")
        if custom_id not in request_mapping:
            continue

        mapping = request_mapping[custom_id]
        filename = mapping["filename"]
        citation_idx = mapping["citation_idx"]
        instance_idx = mapping["instance_idx"]

        if filename not in payloads:
            continue

        # Extract the GPT response
        response = result.get("response", {})
        if response.get("status_code") != 200:
            errors += 1
            continue

        body = response.get("body", {})
        choices = body.get("choices", [])
        if not choices:
            errors += 1
            continue

        content = choices[0].get("message", {}).get("content", "")
        
        # Parse the JSON response
        try:
            # Try to extract JSON from the response
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                gpt_result = json.loads(json_match.group(0))
            else:
                gpt_result = json.loads(content)
        except json.JSONDecodeError:
            errors += 1
            continue

        # Update the instance
        try:
            instance = payloads[filename]["results"][citation_idx]["instances"][instance_idx]
            instance["gpt_relation_score"] = gpt_result.get("relation_score")
            instance["gpt_pinpoint_score"] = gpt_result.get("pinpoint_score")
            instance["gpt_relation_reasoning"] = gpt_result.get("relation_reasoning")
            instance["gpt_reason_code"] = gpt_result.get("reason_code")
            instance["gpt_self_citation"] = bool(gpt_result.get("is_self_citation"))
            instance["gpt_pass"] = 2  # Mark as Pass 2 completed
            
            # Remove the needs_pass2 and in_progress flags since we've now completed it
            if "gpt_needs_pass2" in instance:
                del instance["gpt_needs_pass2"]
            if "gpt_pass2_in_progress" in instance:
                del instance["gpt_pass2_in_progress"]
            
            # Check threshold
            try:
                rel = float(gpt_result.get("relation_score", 1))
                pin = float(gpt_result.get("pinpoint_score", 1))
                if rel < 0.6 or pin < 0.6:
                    instance["gpt_below_threshold"] = True
            except:
                pass
            
            updates_made += 1
        except (IndexError, KeyError):
            errors += 1
            continue

    # Write updates back to database
    print(f"Writing {updates_made} updates to database...")
    with conn.cursor() as cur:
        for filename, payload in payloads.items():
            cur.execute(
                """
                UPDATE file_outputs 
                SET payload = %s, updated_at = NOW()
                WHERE filename = %s;
                """,
                (json.dumps(payload), filename)
            )
    conn.commit()
    conn.close()

    print(f"\n✓ Updated {updates_made} instances in database")
    if errors > 0:
        print(f"✗ {errors} errors encountered")

    return results


def list_batches():
    """List all batches."""
    print("=" * 70)
    print("LISTING ALL BATCHES")
    print("=" * 70)

    client = get_openai_client()
    batches = client.batches.list(limit=20)

    for batch in batches:
        created = datetime.fromtimestamp(batch.created_at).strftime("%Y-%m-%d %H:%M")
        print(f"\n{batch.id}")
        print(f"  Status: {batch.status}")
        print(f"  Created: {created}")
        if batch.request_counts:
            print(f"  Requests: {batch.request_counts.completed}/{batch.request_counts.total}")


def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 batch_gpt.py prepare    - Prepare batch file")
        print("  python3 batch_gpt.py submit     - Prepare and submit batch")
        print("  python3 batch_gpt.py submit-chunk <N> - Submit only N requests (for token limits)")
        print("  python3 batch_gpt.py status <batch_id>   - Check batch status")
        print("  python3 batch_gpt.py retrieve <batch_id> - Retrieve results")
        print("  python3 batch_gpt.py list       - List all batches")
        return

    command = sys.argv[1].lower()

    if command == "prepare":
        batch_file, mapping_file = prepare_batch_file()
        if batch_file:
            print(f"\nTo submit, run: python3 batch_gpt.py submit")

    elif command == "submit":
        batch_file, mapping_file = prepare_batch_file()
        if batch_file:
            batch_id = submit_batch(batch_file)

    elif command == "submit-chunk":
        # Submit only a limited number of requests to stay under token limits
        max_requests = 30  # Default ~30 to stay under 900K tokens
        if len(sys.argv) >= 3:
            try:
                max_requests = int(sys.argv[2])
            except:
                pass
        
        print(f"\nSubmitting chunk of {max_requests} requests...")
        batch_file, mapping_file = prepare_batch_file(max_requests=max_requests)
        if batch_file:
            batch_id = submit_batch(batch_file)
            print(f"\nAfter this batch completes, run 'python3 batch_gpt.py retrieve {batch_id}'")
            print(f"Then run 'python3 batch_gpt.py submit-chunk {max_requests}' for the next chunk")

    elif command == "status":
        if len(sys.argv) < 3:
            print("Usage: python3 batch_gpt.py status <batch_id>")
            return
        check_status(sys.argv[2])

    elif command == "retrieve":
        if len(sys.argv) < 3:
            print("Usage: python3 batch_gpt.py retrieve <batch_id>")
            return
        retrieve_results(sys.argv[2])

    elif command == "list":
        list_batches()

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
