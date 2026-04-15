import json
import os
import re
import urllib.request
import urllib.error
import time


RESULTS_JSON_PATH = os.path.join(os.path.dirname(__file__), "results.json")
OUTPUT_JSONS_FOLDER = os.path.join(os.path.dirname(__file__), "output_jsons")
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def _load_env_key(key_name, env_path=ENV_PATH):
    if not os.path.exists(env_path):
        return ""
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key_name:
                    return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _load_results_index(results_json_path):
    try:
        with open(results_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}, {}

    code_index = {}
    citation_index = {}

    for item in data:
        court_acronym = (item.get("court_acronym") or "").strip()
        if court_acronym and court_acronym.upper() not in {"UNKNOWN", "EMPTY"}:
            code_index.setdefault(court_acronym.upper(), item)

        citation = (item.get("citation") or "").strip()
        if citation:
            citation_index.setdefault(_normalize_citation(citation), item)

        m = re.search(r"\(([^)]+)\)", citation)
        if m:
            code_index.setdefault(m.group(1).strip().upper(), item)

    return code_index, citation_index


def _normalize_citation(citation):
    return citation.replace(" ", "").lower()


def _extract_code(citation):
    m = re.search(r"\(([^)]+)\)", citation)
    if m:
        return m.group(1).strip().upper()

    parts = citation.split()
    if len(parts) >= 2:
        return parts[1].strip().upper()

    return ""


def build_canlii_history_urls(
    citations_json_path,
    results_json_path=RESULTS_JSON_PATH,
    api_key=None,
    rate_limit_seconds=0.5,
):
    """
    Build CanLII API URLs for each citation in a results JSON payload.
    Returns a list of objects containing citation, databaseId, and url.
    """
    if not os.path.exists(citations_json_path):
        alt_path = os.path.join(OUTPUT_JSONS_FOLDER, os.path.basename(citations_json_path))
        if os.path.exists(alt_path):
            citations_json_path = alt_path
    try:
        with open(citations_json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []

    if api_key is None:
        api_key = os.environ.get("CANLII_KEY") or _load_env_key("CANLII_KEY")

    code_index, citation_index = _load_results_index(results_json_path)
    results = []

    for item in payload.get("results", []):
        citation = (item.get("citation") or "").strip()
        if not citation:
            continue

        code = _extract_code(citation)
        entry = code_index.get(code)
        if not entry:
            entry = citation_index.get(_normalize_citation(citation))
        if not entry:
            item["lookup_error"] = "no_matching_result"
            results.append({
                "citation": citation,
                "error": "no_matching_result",
            })
            continue

        database_id = entry.get("databaseId")
        if not database_id:
            item["lookup_error"] = "missing_database_id"
            results.append({
                "citation": citation,
                "error": "missing_database_id",
            })
            continue

        citation_fixed = _normalize_citation(citation)
        api_url = f"https://api.canlii.org/v1/caseBrowse/en/{database_id}/{citation_fixed}/?api_key={api_key}"
        public_url = f"https://api.canlii.org/v1/caseBrowse/en/{database_id}/{citation_fixed}/"
        item["databaseId"] = database_id
        item["api_url"] = public_url
        try:
            with urllib.request.urlopen(api_url) as response:
                payload_bytes = response.read()
            api_payload = json.loads(payload_bytes.decode("utf-8"))
            item["canlii_api_response"] = api_payload
        except urllib.error.HTTPError as e:
            item["api_error"] = f"http_error_{e.code}"
        except Exception:
            item["api_error"] = "request_failed"
        results.append({
            "citation": citation,
            "databaseId": database_id,
            "url": public_url,
        })
        if rate_limit_seconds:
            time.sleep(rate_limit_seconds)

    try:
        with open(citations_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return results
