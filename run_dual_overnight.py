#!/usr/bin/env python3
"""
Dual overnight batch processing - runs OpenAI and Gemini batches in parallel.
OpenAI takes instances 0-24, Gemini takes 25-49, etc.
"""

import os
import sys
import time
import json
import threading
from datetime import datetime

# Import from both batch scripts
from batch_gpt import (
    prepare_batch_file as prepare_openai_batch,
    submit_batch as submit_openai_batch,
    retrieve_results as retrieve_openai_results,
    get_openai_client,
    get_db_conn,
)

from batch_gemini import (
    prepare_gemini_batch,
    submit_gemini_batch,
    retrieve_gemini_results,
    get_gemini_client,
)

# Configuration
OPENAI_CHUNK_SIZE = 25    # OpenAI batch limit
GEMINI_CHUNK_SIZE = 100   # Gemini concurrent batch limit (3M tokens / ~30K per request)
ROUND_SIZE = OPENAI_CHUNK_SIZE + GEMINI_CHUNK_SIZE  # = 125, total per interleave round
CHECK_INTERVAL = 150  # 2.5 minutes
MAX_WAIT_TIME = 7200  # 2 hours

# Lock for thread-safe printing and DB access
print_lock = threading.Lock()


def log(source, message):
    """Thread-safe timestamped logging."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    with print_lock:
        print(f"[{timestamp}] [{source}] {message}")
        sys.stdout.flush()


def get_remaining_count():
    """Query the database for how many instances still need Pass 2."""
    try:
        conn = get_db_conn()
        if not conn:
            return None
        
        with conn.cursor() as cur:
            cur.execute("SELECT filename, payload FROM file_outputs;")
            rows = cur.fetchall()
        conn.close()
        
        remaining = 0
        for filename, payload in rows:
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except:
                    continue
            if not isinstance(payload, dict):
                continue
            
            for item in payload.get("results", []):
                for inst in item.get("instances", []):
                    if inst.get("gpt_needs_pass2"):
                        remaining += 1
        
        return remaining
    except Exception as e:
        return None


def log_remaining(source):
    """Log the remaining Pass 2 count."""
    remaining = get_remaining_count()
    if remaining is not None:
        log(source, f"📊 Remaining instances needing Pass 2: {remaining}")


def run_openai_worker():
    """OpenAI batch worker - always processes from offset 0 since completed items are removed."""
    log("OpenAI", f"Starting OpenAI worker (chunk size: {OPENAI_CHUNK_SIZE})...")
    
    client = get_openai_client()
    processed = 0
    batch_count = 0
    
    while True:
        try:
            # Always use offset 0 - completed items are removed from the pool after retrieve
            log("OpenAI", f"Preparing batch #{batch_count + 1}...")
            batch_file, mapping_file = prepare_openai_batch(max_requests=OPENAI_CHUNK_SIZE, offset=0)
            
            if not batch_file:
                log("OpenAI", "✓ No more instances to process!")
                break
            
            batch_id = submit_openai_batch(batch_file)
            log("OpenAI", f"Submitted: {batch_id}")
            batch_count += 1
            
            # Wait for completion
            wait_start = time.time()
            while True:
                if time.time() - wait_start > MAX_WAIT_TIME:
                    log("OpenAI", f"Timeout waiting for {batch_id}")
                    break
                
                time.sleep(CHECK_INTERVAL)
                
                batch = client.batches.retrieve(batch_id)
                status = batch.status
                
                if batch.request_counts:
                    log("OpenAI", f"Status: {status} ({batch.request_counts.completed}/{batch.request_counts.total})")
                
                if status == "completed":
                    retrieve_openai_results(batch_id, mapping_file)
                    processed += batch.request_counts.completed
                    log("OpenAI", f"✓ Retrieved results! Total processed: {processed}")
                    log_remaining("OpenAI")
                    break
                elif status in ["failed", "cancelled", "expired"]:
                    log("OpenAI", f"✗ Batch {status}")
                    break
            
            time.sleep(10)  # Brief pause before next batch
            
        except Exception as e:
            log("OpenAI", f"ERROR: {e}")
            time.sleep(60)
    
    log("OpenAI", f"Worker finished. Processed: {processed}")


def run_gemini_worker():
    """Gemini batch worker - always processes from offset 0 since completed items are removed."""
    log("Gemini", f"Starting Gemini worker (chunk size: {GEMINI_CHUNK_SIZE})...")
    
    # Brief delay to let OpenAI grab first batch (avoids race condition on first batch)
    time.sleep(10)
    
    client = get_gemini_client()
    processed = 0
    batch_count = 0
    
    while True:
        try:
            # Always use offset 0 - completed items are removed from the pool after retrieve
            log("Gemini", f"Preparing batch #{batch_count + 1}...")
            inline_requests, mapping_file = prepare_gemini_batch(
                max_requests=GEMINI_CHUNK_SIZE,
                offset=0
            )
            
            if not inline_requests:
                log("Gemini", "✓ No more instances to process!")
                break
            
            batch_name = submit_gemini_batch(inline_requests, mapping_file)
            log("Gemini", f"Submitted: {batch_name} ({len(inline_requests)} requests)")
            batch_count += 1
            
            # Wait for completion
            wait_start = time.time()
            completed_states = {'JOB_STATE_SUCCEEDED', 'JOB_STATE_FAILED', 'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED'}
            
            while True:
                if time.time() - wait_start > MAX_WAIT_TIME:
                    log("Gemini", f"Timeout waiting for {batch_name}")
                    break
                
                time.sleep(CHECK_INTERVAL)
                
                batch_job = client.batches.get(name=batch_name)
                status = batch_job.state.name
                log("Gemini", f"Status: {status}")
                
                if status in completed_states:
                    if status == 'JOB_STATE_SUCCEEDED':
                        success = retrieve_gemini_results(batch_name, mapping_file)
                        if success:
                            processed += len(inline_requests)
                            log("Gemini", f"✓ Retrieved results! Total processed: {processed}")
                            log_remaining("Gemini")
                    else:
                        log("Gemini", f"✗ Batch {status}")
                    break
            
            time.sleep(10)
            
        except Exception as e:
            log("Gemini", f"ERROR: {e}")
            time.sleep(60)
    
    log("Gemini", f"Worker finished. Processed: {processed}")


def run_dual_overnight():
    """Run both workers in parallel (safe due to in_progress marking)."""
    log("MAIN", "=" * 70)
    log("MAIN", "DUAL OVERNIGHT BATCH PROCESSING")
    log("MAIN", f"OpenAI chunk: {OPENAI_CHUNK_SIZE}, Gemini chunk: {GEMINI_CHUNK_SIZE}")
    log("MAIN", "Mode: Parallel (instances marked as in_progress)")
    log_remaining("MAIN")  # Show initial count
    log("MAIN", "=" * 70)
    
    # Create and start worker threads
    openai_thread = threading.Thread(target=run_openai_worker, name="OpenAI")
    gemini_thread = threading.Thread(target=run_gemini_worker, name="Gemini")
    
    openai_thread.start()
    gemini_thread.start()
    
    # Wait for both to complete
    openai_thread.join()
    gemini_thread.join()
    
    log("MAIN", "=" * 70)
    log("MAIN", "DUAL PROCESSING COMPLETE")
    log_remaining("MAIN")  # Show final count
    log("MAIN", "=" * 70)


def run_openai_only():
    """Run just OpenAI (for when Gemini API key not available)."""
    log("MAIN", "Running OpenAI only mode...")
    run_openai_worker()


def run_gemini_only():
    """Run just Gemini (for when OpenAI quota exhausted)."""
    log("MAIN", "Running Gemini only mode...")
    run_gemini_worker()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "dual"
    
    if len(sys.argv) > 2:
        try:
            CHUNK_SIZE = int(sys.argv[2])
        except:
            pass
    
    if mode == "openai":
        run_openai_only()
    elif mode == "gemini":
        run_gemini_only()
    else:
        run_dual_overnight()
