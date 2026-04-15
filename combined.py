from canlii_citation_history import build_canlii_history_urls
from hf_citation_history import enrich_with_hf_cases
from parse_pdfs import process_single_pdf
from analyze_gpt import analyze_citations
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
OUTPUT_JSONS_FOLDER = os.path.join(os.path.dirname(__file__), "output_jsons_downloaded")


def _load_env_key(key_name, env_path=ENV_PATH):
    # Read simple KEY=VALUE entries from .env (supports optional "export " prefix)
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


def _get_db_conn():
    # Connect to Postgres using credentials from .env or environment
    try:
        import psycopg2
    except Exception as e:
        raise RuntimeError("psycopg2 is required for DB mode") from e

    host = os.environ.get("DB_HOST") or _load_env_key("DB_HOST")
    port = os.environ.get("DB_PORT") or _load_env_key("DB_PORT") or "5432"
    name = os.environ.get("DB_NAME") or _load_env_key("DB_NAME")
    user = os.environ.get("DB_USER") or _load_env_key("DB_USER")
    password = os.environ.get("DB_PASSWORD") or _load_env_key("DB_PASSWORD")

    if not host or not name or not user or not password:
        raise ValueError("Missing DB_HOST, DB_NAME, DB_USER, or DB_PASSWORD")

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=name,
        user=user,
        password=password,
    )


def _ensure_db_schema(conn):
    # Create table for per-file JSON payloads
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS file_outputs (
                filename TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS file_reports (
                filename TEXT PRIMARY KEY,
                report JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    conn.commit()


def _write_payload_to_db(conn, payload):
    # Upsert the full JSON payload by filename
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO file_outputs (filename, payload, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (filename) DO UPDATE
            SET payload = EXCLUDED.payload, updated_at = NOW();
            """,
            (payload.get("filename"), json.dumps(payload)),
        )
    conn.commit()


def _db_has_file(conn, filename):
    # Check if a file already exists in DB storage
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM file_outputs WHERE filename = %s;", (filename,))
        return cur.fetchone() is not None


def export_from_db(output_folder=OUTPUT_JSONS_FOLDER):
    # Export each DB payload to output_jsons/<filename>.json
    conn = _get_db_conn()
    _ensure_db_schema(conn)
    os.makedirs(output_folder, exist_ok=True)
    with conn.cursor() as cur:
        cur.execute("SELECT filename, payload FROM file_outputs;")
        rows = cur.fetchall()
        cur.execute("SELECT filename, report FROM file_reports;")
        report_rows = cur.fetchall()
    conn.close()

    for filename, payload in rows:
        # Handle JSONB drivers that return strings instead of dicts
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"results": payload}
        if isinstance(payload, list):
            payload = {"results": payload}
        if isinstance(payload, dict) and filename and not payload.get("filename"):
            payload["filename"] = filename
        base = os.path.splitext(os.path.basename(filename))[0]
        output_path = os.path.join(output_folder, f"{base}.json")
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            continue

    # Export combined reports.json
    reports = []
    for filename, report in report_rows:
        if isinstance(report, str):
            try:
                report = json.loads(report)
            except Exception:
                report = {"filename": filename, "report": report}
        if isinstance(report, dict) and filename and not report.get("filename"):
            report["filename"] = filename
        reports.append(report)
    try:
        reports_path = os.path.join(output_folder, "reports.json")
        with open(reports_path, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _print_summary(output_json, threshold=0.6):
    try:
        with open(output_json, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        print(f"Summary error: {e}")
        return

    total = payload.get("total_citations")
    unique = payload.get("unique_citations")

    low_citations = set()
    for item in payload.get("results", []):
        citation = item.get("citation")
        if not citation:
            continue
        for inst in item.get("instances", []):
            rel = inst.get("gpt_relation_score")
            pin = inst.get("gpt_pinpoint_score")
            try:
                if rel is not None and float(rel) < threshold:
                    low_citations.add(citation)
                if pin is not None and float(pin) < threshold:
                    low_citations.add(citation)
            except Exception:
                continue

    print(f"Citations total: {total}")
    print(f"Citations unique: {unique}")
    if low_citations:
        print(f"Citations under {threshold}: {sorted(low_citations)}")
    else:
        print(f"Citations under {threshold}: []")


def run_pipeline(mode, path, storage_mode="LOCAL", pdf_max_workers=2, gpt_max_workers=6, skip_duplicate=False):
    """
    mode: "SINGLE" or "MANY"
    path: pdf path (SINGLE) or folder path (MANY)
    """
    # Normalize mode input
    mode = (mode or "").strip().upper()
    if mode == "SINGLE":
        # Single PDF path
        pdf_paths = [path]
    elif mode == "MANY":
        # Load all PDFs from folder
        pdf_paths = [
            os.path.join(path, name)
            for name in os.listdir(path)
            if name.lower().endswith(".pdf")
        ]
    else:
        raise ValueError('mode must be "SINGLE" or "MANY"')

    storage_mode = (storage_mode or "LOCAL").strip().upper()
    if storage_mode not in {"LOCAL", "DB"}:
        raise ValueError('storage_mode must be "LOCAL" or "DB"')

    # Reuse HF dataset/citation caches across the pipeline run
    hf_dataset_cache = {}
    hf_citation_cache = {}
    hf_cache_lock = threading.Lock()

    db_conn = None
    if storage_mode == "DB":
        db_conn = _get_db_conn()
        _ensure_db_schema(db_conn)
        print("DB connected and schema ensured")

    # Pre-filter duplicates so we don't queue them
    if skip_duplicate and pdf_paths:
        filtered = []
        for pdf_path in pdf_paths:
            output_json = os.path.join(
                os.path.dirname(__file__),
                "output_jsons",
                f"{os.path.splitext(os.path.basename(pdf_path))[0]}.json",
            )
            if os.path.exists(output_json):
                continue
            if storage_mode == "DB" and db_conn is not None:
                try:
                    if _db_has_file(db_conn, os.path.basename(output_json)):
                        continue
                except Exception:
                    pass
            filtered.append(pdf_path)
        pdf_paths = filtered

    print(f"run_pipeline start: mode={mode} storage_mode={storage_mode} pdfs={len(pdf_paths)}")

    def _process_pdf(pdf_path):
        if not pdf_path:
            return None
        output_json = os.path.join(
            os.path.dirname(__file__),
            "output_jsons",
            f"{os.path.splitext(os.path.basename(pdf_path))[0]}.json",
        )
        if skip_duplicate:
            if os.path.exists(output_json):
                return output_json
            if storage_mode == "DB" and db_conn is not None:
                try:
                    if _db_has_file(db_conn, os.path.basename(output_json)):
                        return output_json
                except Exception:
                    pass
        print(f"Processing: {os.path.basename(pdf_path)}")
        # Parse citations + summary
        result = process_single_pdf(pdf_path)
        if not result:
            return None
        # Enrich with CanLII metadata
        build_canlii_history_urls(output_json)
        # Enrich with HF data (cached per run, thread-safe)
        enrich_with_hf_cases(
            output_json,
            dataset_cache=hf_dataset_cache,
            citation_cache=hf_citation_cache,
            cache_lock=hf_cache_lock,
        )
        # Score citation usage with GPT + prune self-citations
        analyze_citations(
            output_json,
            gpt_max_workers=gpt_max_workers,
            rate_limit_seconds=0.0,
            report_mode="DB" if storage_mode == "DB" else "FILE",
            report_db_conn=db_conn,
        )
        # Print per-file summary
        _print_summary(output_json)

        # Persist to DB if configured
        if storage_mode == "DB" and db_conn is not None:
            try:
                with open(output_json, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                _write_payload_to_db(db_conn, payload)
            except Exception:
                pass
        return output_json

    with ThreadPoolExecutor(max_workers=pdf_max_workers) as executor:
        futures = [executor.submit(_process_pdf, path) for path in pdf_paths]
        for future in as_completed(futures):
            _ = future.result()

    if db_conn is not None:
        db_conn.close()


    #run_pipeline("MANY", "/Users/eladdekel/Desktop/Mazaheri, 25H-139/1. Licensee Submissions", storage_mode="DB", skip_duplicate=True)

