import json
import os
import re


ALLOWED_CODES = {
    "SCC", "FCA", "FC", "TCC", "CMAC", "CHRT", "SST", "RPD", "RAD", "RLLR",
    "BCCA", "BCSC", "ONCA", "YKCA",
}

CASE_TEXTS_PATH = os.path.join(os.path.dirname(__file__), "case_texts.json")
OUTPUT_JSONS_FOLDER = os.path.join(os.path.dirname(__file__), "output_jsons")
_DATASET_CACHE = {}
_CITATION_CACHE = {}


def _json_safe(value):
    if value is None:
        return None
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


def _extract_code(citation):
    m = re.search(r"\(([^)]+)\)", citation)
    if m:
        return m.group(1).strip().upper()

    parts = citation.split()
    if len(parts) >= 2:
        return parts[1].strip().upper()

    return ""


def _load_case_texts(case_texts_path):
    if not os.path.exists(case_texts_path):
        return []
    try:
        with open(case_texts_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        return []
    return []


def _case_texts_has_citation(case_texts, citation):
    for item in case_texts:
        if isinstance(item, dict) and item.get("citation") == citation:
            return True
    return False


def enrich_with_hf_cases(
    citations_json_path,
    case_texts_path=CASE_TEXTS_PATH,
    dataset_cache=None,
    citation_cache=None,
    cache_lock=None,
):
    """
    For citations in the provided JSON, look up matching rows in the
    a2aj/canadian-case-law Hugging Face dataset and write results in-place.
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

    from datasets import load_dataset

    if dataset_cache is None:
        dataset_cache = _DATASET_CACHE
    if citation_cache is None:
        citation_cache = _CITATION_CACHE
    results = []
    case_texts = _load_case_texts(case_texts_path)

    for item in payload.get("results", []):
        citation = (item.get("citation") or "").strip()
        if not citation:
            continue

        code = _extract_code(citation)
        if code not in ALLOWED_CODES:
            item["hf_error"] = "unsupported_court_code"
            results.append({"citation": citation, "error": "unsupported_court_code"})
            continue

        cache_key = (code, citation)
        if cache_lock:
            cache_lock.acquire()
        try:
            cached = citation_cache.get(cache_key)
        finally:
            if cache_lock:
                cache_lock.release()
        if cached is not None:
            if cached.get("hf_result"):
                item["hf_result"] = cached["hf_result"]
                results.append({"citation": citation, "hf_result": cached["hf_result"]})
            if cached.get("hf_error"):
                item["hf_error"] = cached["hf_error"]
                results.append({"citation": citation, "error": cached["hf_error"]})
            if cached.get("unofficial_text"):
                if not _case_texts_has_citation(case_texts, citation):
                    case_texts.append({
                        "citation": citation,
                        "case_text": cached["unofficial_text"],
                    })
            continue

        if cache_lock:
            cache_lock.acquire()
        try:
            has_dataset = code in dataset_cache
        finally:
            if cache_lock:
                cache_lock.release()
        if not has_dataset:
            # Use streaming to avoid loading the entire split into RAM
            cases = load_dataset("a2aj/canadian-case-law", data_dir=code, split="train", streaming=True)
            if cache_lock:
                cache_lock.acquire()
            try:
                dataset_cache[code] = cases
            finally:
                if cache_lock:
                    cache_lock.release()

        if cache_lock:
            cache_lock.acquire()
        try:
            dataset = dataset_cache[code]
        finally:
            if cache_lock:
                cache_lock.release()

        match_row = None
        for row in dataset:
            if (row.get("citation_en") == citation) or (row.get("citation2_en") == citation):
                match_row = row
                break

        if match_row is None:
            item["hf_error"] = "citation_not_found"
            if cache_lock:
                cache_lock.acquire()
            try:
                citation_cache[cache_key] = {"hf_error": "citation_not_found"}
            finally:
                if cache_lock:
                    cache_lock.release()
            results.append({"citation": citation, "error": "citation_not_found"})
            continue

        row = match_row
        hf_result = {
            "dataset": _json_safe(row.get("dataset")),
            "citation_en": _json_safe(row.get("citation_en")),
            "citation2_en": _json_safe(row.get("citation2_en")),
            "name_en": _json_safe(row.get("name_en")),
            "document_date_en": _json_safe(row.get("document_date_en")),
            "url_en": _json_safe(row.get("url_en")),
        }
        item["hf_result"] = hf_result
        results.append({"citation": citation, "hf_result": hf_result})

        unofficial_text = _json_safe(row.get("unofficial_text_en"))
        if cache_lock:
            cache_lock.acquire()
        try:
            citation_cache[cache_key] = {
                "hf_result": hf_result,
                "unofficial_text": unofficial_text,
            }
        finally:
            if cache_lock:
                cache_lock.release()
        if unofficial_text and not _case_texts_has_citation(case_texts, citation):
            case_texts.append({
                "citation": citation,
                "case_text": unofficial_text,
            })

    try:
        tmp_path = f"{citations_json_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, citations_json_path)
    except Exception:
        pass

    if case_texts:
        try:
            tmp_path = f"{case_texts_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(case_texts, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, case_texts_path)
        except Exception:
            pass

    return results
