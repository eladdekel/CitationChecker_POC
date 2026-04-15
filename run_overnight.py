#!/usr/bin/env python3
"""
Automated overnight batch processing script.
Continuously submits batches, waits for completion, retrieves results, and repeats.
"""

import os
import sys
import time
from datetime import datetime

# Import functions from batch_gpt
from batch_gpt import (
    prepare_batch_file,
    submit_batch,
    check_status,
    retrieve_results,
    get_openai_client,
)

# Configuration
CHUNK_SIZE = 25  # Number of requests per batch
CHECK_INTERVAL = 300  # 5 minutes in seconds
MAX_WAIT_TIME = 7200  # 2 hours max wait per batch before giving up


def log(message):
    """Print timestamped log message."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
    sys.stdout.flush()


def get_batch_status(batch_id):
    """Get the current status of a batch."""
    client = get_openai_client()
    batch = client.batches.retrieve(batch_id)
    return batch


def run_overnight():
    log("=" * 70)
    log("OVERNIGHT BATCH PROCESSING")
    log(f"Chunk size: {CHUNK_SIZE}, Check interval: {CHECK_INTERVAL}s")
    log("=" * 70)
    
    total_processed = 0
    total_failed = 0
    batch_count = 0
    
    while True:
        # Step 1: Prepare and submit a chunk
        log(f"\n--- Batch #{batch_count + 1} ---")
        log(f"Preparing batch of {CHUNK_SIZE} requests...")
        
        try:
            batch_file, mapping_file = prepare_batch_file(max_requests=CHUNK_SIZE)
        except Exception as e:
            log(f"ERROR preparing batch: {e}")
            break
        
        if not batch_file:
            log("✓ No more instances need processing. All done!")
            break
        
        # Submit the batch
        try:
            batch_id = submit_batch(batch_file)
            log(f"Submitted batch: {batch_id}")
            batch_count += 1
        except Exception as e:
            log(f"ERROR submitting batch: {e}")
            log("Waiting 5 minutes before retrying...")
            time.sleep(CHECK_INTERVAL)
            continue
        
        # Step 2: Wait for completion
        wait_start = time.time()
        while True:
            elapsed = time.time() - wait_start
            
            if elapsed > MAX_WAIT_TIME:
                log(f"WARNING: Batch {batch_id} exceeded max wait time. Moving on...")
                break
            
            log(f"Checking status of {batch_id}...")
            
            try:
                batch = get_batch_status(batch_id)
                status = batch.status
                
                if batch.request_counts:
                    completed = batch.request_counts.completed
                    failed = batch.request_counts.failed
                    total = batch.request_counts.total
                    log(f"  Status: {status} | Progress: {completed}/{total} completed, {failed} failed")
                else:
                    log(f"  Status: {status}")
                
                if status == "completed":
                    log(f"✓ Batch completed!")
                    
                    # Retrieve results
                    try:
                        retrieve_results(batch_id)
                        total_processed += batch.request_counts.completed
                        total_failed += batch.request_counts.failed
                        log(f"✓ Results retrieved and saved to database")
                    except Exception as e:
                        log(f"ERROR retrieving results: {e}")
                    
                    break
                
                elif status == "failed":
                    log(f"✗ Batch failed!")
                    if batch.errors:
                        log(f"  Errors: {batch.errors}")
                    total_failed += batch.request_counts.total if batch.request_counts else CHUNK_SIZE
                    break
                
                elif status in ["cancelled", "expired"]:
                    log(f"✗ Batch {status}")
                    break
                
                else:
                    # Still processing, wait and check again
                    log(f"  Waiting {CHECK_INTERVAL}s before next check...")
                    time.sleep(CHECK_INTERVAL)
                    
            except Exception as e:
                log(f"ERROR checking status: {e}")
                log(f"Waiting {CHECK_INTERVAL}s before retry...")
                time.sleep(CHECK_INTERVAL)
        
        # Small delay before next batch
        log("Waiting 10s before next batch...")
        time.sleep(10)
    
    # Summary
    log("\n" + "=" * 70)
    log("OVERNIGHT PROCESSING COMPLETE")
    log("=" * 70)
    log(f"  Total batches: {batch_count}")
    log(f"  Total processed: {total_processed}")
    log(f"  Total failed: {total_failed}")
    log("=" * 70)


if __name__ == "__main__":
    # Allow overriding chunk size via command line
    if len(sys.argv) >= 2:
        try:
            CHUNK_SIZE = int(sys.argv[1])
        except:
            pass
    
    if len(sys.argv) >= 3:
        try:
            CHECK_INTERVAL = int(sys.argv[2])
        except:
            pass
    
    run_overnight()
