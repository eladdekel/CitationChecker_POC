import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai_connect import run_prompt


CASE_TEXTS_PATH = os.path.join(os.path.dirname(__file__), "case_texts.json")
OUTPUT_JSONS_FOLDER = os.path.join(os.path.dirname(__file__), "output_jsons")
CURRENT_YEAR = 2026


def _load_case_texts(case_texts_path):
    # Load case_texts.json into a citation -> text map for quick lookup
    if not os.path.exists(case_texts_path):
        return {}
    try:
        with open(case_texts_path, "r", encoding="utf-8") as f:
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


def _extract_json(text):
    # Prefer strict JSON, fall back to first JSON object if needed
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _tokenize(text):
    # Lightweight tokenization for overlap checks
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _overlap_score(text_a, text_b):
    # Simple Jaccard overlap to estimate topical alignment
    set_a = set(_tokenize(text_a))
    set_b = set(_tokenize(text_b))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / max(1, len(set_a | set_b))


def _year_from_citation(citation):
    match = re.match(r"^(1[6-9]\d{2}|20[0-2]\d)", citation.strip())
    return match.group(1) if match else ""


def _year_from_date(date_str):
    if not date_str:
        return ""
    match = re.search(r"(\d{4})", str(date_str))
    return match.group(1) if match else ""


def _extract_court_code(citation):
    # Extract court code from "YYYY CODE NNN" or "YYYY CanLII NNN (CODE)"
    match = re.search(r"\(([^)]+)\)", citation)
    if match:
        return match.group(1).strip().upper()
    parts = citation.split()
    if len(parts) >= 2:
        return parts[1].strip().upper()
    return ""


def _extract_court_codes_from_court_no(court_no):
    # Extract uppercase court codes from the court number field
    return set(re.findall(r"\b[A-Z]{2,6}\b", court_no or ""))


def _normalize_simple(text):
    # Strip to alphanumerics for fuzzy docket/citation matching
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _expand_pinpoints(pinpoints):
    # Expand pinpoint ranges like "12-14" into individual ints
    expanded = []
    for p in pinpoints:
        if not p:
            continue
        if "-" in p:
            start, end = p.split("-", 1)
            try:
                s = int(start.strip())
                e = int(end.strip())
                for n in range(s, e + 1):
                    expanded.append(n)
            except Exception:
                continue
        else:
            try:
                expanded.append(int(p.strip()))
            except Exception:
                continue
    return expanded


def _pinpoint_in_text(number, text):
    # Check if a paragraph number appears in typical legal formats
    if not text:
        return False
    patterns = [
        rf"\[\s*{number}\s*\]",
        rf"\bpara(?:graph)?\s*{number}\b",
        rf"\b{number}\b",
    ]
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def _age_mismatch(citation_year, paragraph):
    # Flag if paragraph years are far from citation year, or paragraph suggests recency
    if not citation_year:
        return False
    try:
        citation_year_int = int(citation_year)
    except Exception:
        return False
    years_in_para = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", paragraph)]
    if years_in_para:
        min_diff = min(abs(y - citation_year_int) for y in years_in_para)
        if min_diff >= 15:
            return True
    if re.search(r"\b(recent|new|current|latest|modern)\b", paragraph, re.IGNORECASE):
        if CURRENT_YEAR - citation_year_int >= 10:
            return True
    return False


def _write_report(json_path, payload, report_mode="FILE", report_db_conn=None):
    # Write a compact per-file report to output_jsons or a DB table
    base = os.path.splitext(os.path.basename(json_path))[0]
    report_path = os.path.join(OUTPUT_JSONS_FOLDER, f"{base}_report.json")
    reason_counts = {}
    low_count = 0
    self_citation_count = 0
    for item in payload.get("results", []):
        for inst in item.get("instances", []):
            reason = inst.get("gpt_reason_code") or "unknown"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if inst.get("gpt_below_threshold"):
                low_count += 1
            if inst.get("gpt_self_citation"):
                self_citation_count += 1
    report = {
        "filename": payload.get("filename"),
        "unique_citations": payload.get("unique_citations"),
        "total_citations": payload.get("total_citations"),
        "low_score_instances": low_count,
        "self_citation_instances": self_citation_count,
        "reason_code_counts": reason_counts,
    }
    if report_mode == "DB" and report_db_conn is not None:
        try:
            with report_db_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO file_reports (filename, report, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (filename) DO UPDATE
                    SET report = EXCLUDED.report, updated_at = NOW();
                    """,
                    (payload.get("filename"), json.dumps(report)),
                )
            report_db_conn.commit()
        except Exception:
            pass
    else:
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _score_instance(
    citation,
    paragraph,
    pinpoints,
    case_text,
    file_summary,
    keywords,
    topics,
    canlii_title,
    canlii_date,
    hf_name,
    hf_date,
    metadata_flags,
    title_overlap,
    keyword_overlap,
    pinpoint_mismatch,
    out_of_jurisdiction,
    age_mismatch_flag,
    self_citation_by_docket,
    threshold,
    pass1_only=False,
):
    # Shared context block for both passes
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

    pass1_prompt = (
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

    parsed1 = None
    try:
        reply1 = run_prompt(pass1_prompt)
        parsed1 = _extract_json(reply1)
    except Exception as e:
        return {"error": f"request_failed: {e}"}

    need_pass2 = False
    if not isinstance(parsed1, dict):
        need_pass2 = True
    else:
        try:
            rel1 = float(parsed1.get("relation_score", 1))
            pin1 = float(parsed1.get("pinpoint_score", 1))
        except Exception:
            rel1 = 1
            pin1 = 1
        # Only flag for Pass 2 if scores are actually below threshold
        # Don't flag based on reason codes alone - if scores are high, Pass 1 is sufficient
        if rel1 < threshold or pin1 < threshold:
            need_pass2 = True

    parsed = parsed1
    gpt_pass = 1
    
    # If pass1_only mode, return Pass 1 results with need_pass2 flag
    if pass1_only:
        if not isinstance(parsed, dict):
            return {"error": "invalid_response_format", "gpt_pass": gpt_pass, "need_pass2": need_pass2}
        return {
            "relation_score": parsed.get("relation_score"),
            "pinpoint_score": parsed.get("pinpoint_score"),
            "relation_reasoning": parsed.get("relation_reasoning"),
            "reason_code": parsed.get("reason_code"),
            "is_self_citation": bool(parsed.get("is_self_citation")),
            "gpt_pass": gpt_pass,
            "need_pass2": need_pass2 and bool(case_text),  # Only need pass2 if case_text exists
        }
    
    # Normal mode: continue to Pass 2 if needed
    if need_pass2 and case_text:
        pass2_prompt = (
            "You are evaluating whether a legal citation is used correctly.\n"
            "Return ONLY a JSON object with keys:\n"
            "relation_score (0-1), pinpoint_score (0-1), relation_reasoning (string), "
            "reason_code (string), is_self_citation (true/false).\n"
            "reason_code must be one of: keyword_mismatch, topic_mismatch, title_mismatch, "
            "year_mismatch, pinpoint_mismatch, out_of_jurisdiction, age_mismatch, "
            "self_citation, self_citation_docket, missing_metadata, low_overlap, below_threshold, other.\n\n"
            f"{context_block}"
            "Citation text:\n"
            f"{case_text}\n\n"
            "Task:\n"
            "1) relation_score: how well the citation matches the document's use.\n"
            "2) pinpoint_score: whether the pinpoint usage is correct.\n"
            "3) relation_reasoning: brief justification for relation_score.\n"
            "4) reason_code: pick the best single code.\n"
            "5) is_self_citation: true if the citation refers to this document itself.\n"
        )
        try:
            reply2 = run_prompt(pass2_prompt)
            parsed2 = _extract_json(reply2)
            if isinstance(parsed2, dict):
                parsed = parsed2
                gpt_pass = 2
        except Exception:
            pass

    if not isinstance(parsed, dict):
        return {"error": "invalid_response_format", "gpt_pass": gpt_pass}

    return {
        "relation_score": parsed.get("relation_score"),
        "pinpoint_score": parsed.get("pinpoint_score"),
        "relation_reasoning": parsed.get("relation_reasoning"),
        "reason_code": parsed.get("reason_code"),
        "is_self_citation": bool(parsed.get("is_self_citation")),
        "gpt_pass": gpt_pass,
    }


def analyze_citations(
    json_path,
    case_texts_path=CASE_TEXTS_PATH,
    rate_limit_seconds=0.05,
    threshold=0.6,
    gpt_max_workers=6,
    report_mode="FILE",
    report_db_conn=None,
    pass1_only=False,
):
    """
    For each citation instance in the JSON, ask the model to score:
      - relation_score: how well the citation matches the document usage
      - pinpoint_score: whether the pinpoint usage is correct
    Adds results to each instance as:
      gpt_relation_score, gpt_pinpoint_score, gpt_relation_reasoning
    
    If pass1_only=True, only runs Pass 1 and marks instances that need Pass 2
    with gpt_needs_pass2=True for later batch processing.
    """
    if not os.path.exists(json_path):
        alt_path = os.path.join(OUTPUT_JSONS_FOLDER, os.path.basename(json_path))
        if os.path.exists(alt_path):
            json_path = alt_path
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []

    case_texts = _load_case_texts(case_texts_path)
    file_summary = payload.get("file_ai_summary") or payload.get("FILE_AI_SUMMARY", "")
    results = []

    # Count total instances for progress logging
    total_instances = sum(len(item.get("instances", [])) for item in payload.get("results", []))
    processed_count = 0
    filename = os.path.basename(json_path)
    print(f"  📊 {filename}: {total_instances} instances to process")

    # Track self-citation removals after scoring
    citations_to_remove = set()
    # Document-level court number for jurisdiction checks
    doc_court_no = payload.get("court_no", "")
    doc_court_codes = _extract_court_codes_from_court_no(doc_court_no)

    for citation_item in payload.get("results", []):
        citation = (citation_item.get("citation") or "").strip()
        if not citation:
            continue

        canlii_resp = citation_item.get("canlii_api_response") or {}
        keywords = canlii_resp.get("keywords", "")
        topics = canlii_resp.get("topics", "")
        canlii_title = canlii_resp.get("title", "")
        canlii_date = canlii_resp.get("decisionDate", "")
        hf_result = citation_item.get("hf_result") or {}
        hf_name = hf_result.get("name_en", "")
        hf_date = hf_result.get("document_date_en", "")
        case_text = case_texts.get(citation, "")

        # Court code for jurisdiction checks
        citation_code = _extract_court_code(citation)
        out_of_jurisdiction = bool(doc_court_codes) and citation_code not in doc_court_codes and citation_code != "SCC"
        self_citation_by_docket = _normalize_simple(citation) in _normalize_simple(doc_court_no)

        # Cross-check years and title overlap for mismatch flags
        citation_year = _year_from_citation(citation)
        canlii_year = _year_from_date(canlii_date)
        hf_year = _year_from_date(hf_date)
        metadata_flags = []
        if citation_year and canlii_year and citation_year != canlii_year:
            metadata_flags.append("year_mismatch_canlii")
        if citation_year and hf_year and citation_year != hf_year:
            metadata_flags.append("year_mismatch_hf")
        if canlii_title and hf_name:
            title_overlap = _overlap_score(canlii_title, hf_name)
            if title_overlap < 0.2:
                metadata_flags.append("title_mismatch")
        else:
            title_overlap = 0.0

        # Prepare GPT tasks for this citation's instances
        futures = []
        instance_map = {}
        with ThreadPoolExecutor(max_workers=gpt_max_workers) as executor:
            for instance in citation_item.get("instances", []):
                paragraph = (instance.get("paragraph") or "").strip()
                if not paragraph:
                    continue

                # Quick overlap check for relevance signal
                keyword_overlap = _overlap_score(paragraph, f"{keywords} {topics} {canlii_title} {hf_name}")
                instance["keyword_overlap"] = keyword_overlap

                # Confidence gate: no metadata and no case text
                if not canlii_resp and not case_text:
                    instance["gpt_relation_score"] = 0.0
                    instance["gpt_pinpoint_score"] = 0.0
                    instance["gpt_relation_reasoning"] = "missing_metadata"
                    instance["gpt_reason_code"] = "missing_metadata"
                    instance["gpt_self_citation"] = False
                    results.append({
                        "citation": citation,
                        "error": "missing_metadata",
                    })
                    continue

                pinpoints = instance.get("pinpoints", [])
                expanded_pinpoints = _expand_pinpoints(pinpoints)
                missing_pinpoints = [p for p in expanded_pinpoints if not _pinpoint_in_text(p, case_text)]
                pinpoint_mismatch = bool(expanded_pinpoints and missing_pinpoints)
                instance["pinpoint_validation"] = {
                    "pinpoints": pinpoints,
                    "missing": missing_pinpoints,
                    "found": False if pinpoint_mismatch else bool(expanded_pinpoints),
                }
                age_mismatch_flag = _age_mismatch(citation_year, paragraph)
                instance["age_mismatch_flag"] = age_mismatch_flag
                instance["out_of_jurisdiction_flag"] = out_of_jurisdiction
                instance["self_citation_docket_flag"] = self_citation_by_docket

                future = executor.submit(
                    _score_instance,
                    citation,
                    paragraph,
                    pinpoints,
                    case_text,
                    file_summary,
                    keywords,
                    topics,
                    canlii_title,
                    canlii_date,
                    hf_name,
                    hf_date,
                    metadata_flags,
                    title_overlap,
                    keyword_overlap,
                    pinpoint_mismatch,
                    out_of_jurisdiction,
                    age_mismatch_flag,
                    self_citation_by_docket,
                    threshold,
                    pass1_only,
                )
                futures.append(future)
                instance_map[future] = (instance, pinpoint_mismatch, age_mismatch_flag, out_of_jurisdiction, self_citation_by_docket)

            for future in as_completed(futures):
                instance, pinpoint_mismatch, age_mismatch_flag, out_of_jurisdiction, self_citation_by_docket = instance_map[future]
                result = future.result()
                if result.get("error"):
                    instance["gpt_relation_score"] = None
                    instance["gpt_pinpoint_score"] = None
                    instance["gpt_relation_reasoning"] = result.get("error")
                    instance["gpt_reason_code"] = "other"
                    instance["gpt_self_citation"] = False
                    instance["gpt_pass"] = result.get("gpt_pass", 1)
                    results.append({
                        "citation": citation,
                        "error": result.get("error"),
                    })
                else:
                    instance["gpt_relation_score"] = result.get("relation_score")
                    instance["gpt_pinpoint_score"] = result.get("pinpoint_score")
                    instance["gpt_relation_reasoning"] = result.get("relation_reasoning")
                    instance["gpt_reason_code"] = result.get("reason_code")
                    instance["gpt_self_citation"] = bool(result.get("is_self_citation"))
                    instance["gpt_pass"] = result.get("gpt_pass", 1)
                    
                    # In pass1_only mode, mark instances that need Pass 2
                    if pass1_only and result.get("need_pass2"):
                        instance["gpt_needs_pass2"] = True
                    results.append({
                        "citation": citation,
                        "relation_score": result.get("relation_score"),
                        "pinpoint_score": result.get("pinpoint_score"),
                    })

                    # Threshold-based reason code + flag
                    try:
                        if float(result.get("relation_score", 1)) < threshold or float(result.get("pinpoint_score", 1)) < threshold:
                            instance["gpt_below_threshold"] = True
                            if not instance.get("gpt_reason_code"):
                                instance["gpt_reason_code"] = "below_threshold"
                    except Exception:
                        pass

                    # Heuristic reason codes for structural checks
                    if pinpoint_mismatch and not instance.get("gpt_reason_code"):
                        instance["gpt_reason_code"] = "pinpoint_mismatch"
                    if out_of_jurisdiction and not instance.get("gpt_reason_code"):
                        instance["gpt_reason_code"] = "out_of_jurisdiction"
                    if age_mismatch_flag and not instance.get("gpt_reason_code"):
                        instance["gpt_reason_code"] = "age_mismatch"

                    # Mark self-citations for removal (GPT or docket heuristic)
                    if instance.get("gpt_self_citation") or self_citation_by_docket:
                        instance["gpt_self_citation"] = True
                        instance["gpt_reason_code"] = "self_citation_docket" if self_citation_by_docket else "self_citation"

                if rate_limit_seconds:
                    time.sleep(rate_limit_seconds)
                
                # Progress logging
                processed_count += 1
                if processed_count % 10 == 0 or processed_count == total_instances:
                    pct = (processed_count / total_instances * 100) if total_instances > 0 else 100
                    print(f"  ⏳ {filename}: {processed_count}/{total_instances} ({pct:.0f}%)")

        # Remove self-citation instances; drop citation if none remain
        remaining_instances = [i for i in citation_item.get("instances", []) if not i.get("gpt_self_citation")]
        if not remaining_instances and citation_item.get("instances"):
            citations_to_remove.add(citation)
        else:
            citation_item["instances"] = remaining_instances

    # Remove citations that were only self-citations
    if citations_to_remove:
        payload["results"] = [c for c in payload.get("results", []) if c.get("citation") not in citations_to_remove]

    # Recalculate totals after removals
    payload["unique_citations"] = len(payload.get("results", []))
    payload["total_citations"] = sum(len(c.get("instances", [])) for c in payload.get("results", []))

    # Emit a compact per-file report for quick triage
    _write_report(json_path, payload, report_mode=report_mode, report_db_conn=report_db_conn)

    try:
        tmp_path = f"{json_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, json_path)
    except Exception:
        pass

    return results
