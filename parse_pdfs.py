import re
import pdfplumber
import os
import json
from openai_connect import run_prompt
import csv


# Step 1 - Take a PDF and look for the pattern. 

RESULTS_JSON_PATH = os.path.join(os.path.dirname(__file__), "results.json")
FILE_INFO_FOLDER = os.path.join(os.path.dirname(__file__), "file_info_mapper")
OUTPUT_JSONS_FOLDER = os.path.join(os.path.dirname(__file__), "output_jsons")


def _load_file_info_map(folder_path):
    # Build a map from FOREMOST_NUMBER -> row data across all CSVs in the folder
    info_map = {}
    if not os.path.isdir(folder_path):
        return info_map
    for name in os.listdir(folder_path):
        if not name.lower().endswith(".csv"):
            continue
        csv_path = os.path.join(folder_path, name)
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = (row.get("FOREMOST_NUMBER") or "").strip()
                    if not key:
                        continue
                    info_map[key] = row
        except Exception:
            continue
    return info_map

def _load_court_codes():
    """
    Load court acronyms from results.json and return a tuple of:
    - direct court codes (e.g., ABCA)
    - parenthetical CanLII codes (e.g., ON SCSM)
    """
    try:
        with open(RESULTS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], []

    direct_codes = set()
    parenthetical_codes = set()

    for item in data:
        code = (item.get("court_acronym") or "").strip()
        if code and code.upper() not in {"UNKNOWN", "EMPTY"}:
            direct_codes.add(code)

        citation = (item.get("citation") or "").strip()
        # Capture codes inside parentheses for CanLII-style citations
        m = re.search(r"\(([^)]+)\)", citation)
        if m:
            parenthetical_codes.add(m.group(1).strip())

    return sorted(direct_codes), sorted(parenthetical_codes)


court_codes, canlii_parenthetical_codes = _load_court_codes()
canlii_base_url = "https://api.canlii.org/v1/caseBrowse/en/?api_key="


def _normalize_citation(citation):
    # Canonical form for matching the same citation across sources
    return re.sub(r"\s+", "", citation).lower()


def _extract_pinpoints(paragraph):
    # Capture common pinpoint formats like "para 12", "paras 12-14", "p 123", "pp 12-13"
    pinpoints = []
    patterns = [
        r"\b(?:para|paras|paragraph|paragraphs)\.?\s+(\d+(?:\s*-\s*\d+)?)\b",
        r"\b(?:at\s+)?p\.?\s*(\d+(?:\s*-\s*\d+)?)\b",
        r"\b(?:at\s+)?pp\.?\s*(\d+(?:\s*-\s*\d+)?)\b",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, paragraph, flags=re.IGNORECASE):
            pinpoints.append(match)
    return pinpoints


def process_single_pdf(file_path):
    """
    Parses a single PDF for citations and returns a list of 
    dictionaries containing the filename, citation, and paragraph context.
    """
    filename = os.path.basename(file_path)
    os.makedirs(OUTPUT_JSONS_FOLDER, exist_ok=True)
    output_path = os.path.join(
        OUTPUT_JSONS_FOLDER,
        f"{os.path.splitext(filename)[0]}.json",
    )
    file_info_map = _load_file_info_map(FILE_INFO_FOLDER)
    file_key = os.path.splitext(filename)[0]
    file_info_row = file_info_map.get(file_key, {})
    # Build regexes for:
    # 1) Direct format: "YYYY CODE 1234"
    # 2) CanLII format: "YYYY CanLII 1234 (CODE)"
    direct_codes_regex = "|".join(re.escape(code) for code in court_codes) or r"(?!x)x"
    parenthetical_codes_regex = "|".join(re.escape(code) for code in canlii_parenthetical_codes) or r"(?!x)x"

    year_regex = r"(?:1[6-9]\d{2}|20[0-2][0-6])"
    direct_citation_pattern = rf"\b({year_regex})\s+({direct_codes_regex})\s+(\d{{1,6}})\b"
    canlii_citation_pattern = rf"\b({year_regex})\s+CanLII\s+(\d{{1,6}})\s+\(({parenthetical_codes_regex})\)\b"
    
    # Store all citation hits grouped by citation
    matches_by_citation = {}
    # Collect full text for AI summary
    full_text_parts = []

    try:
        with pdfplumber.open(file_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if not text:
                    continue
                full_text_parts.append(text)
                
                # Split text into paragraphs based on double newlines
                # We strip leading/trailing whitespace to avoid empty blocks
                paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                
                for para in paragraphs:
                    # Replace single newlines with spaces to fix PDF "mid-sentence" breaks
                    clean_para = para.replace('\n', ' ')
                    
                    found_citations = []

                    for match in re.findall(direct_citation_pattern, clean_para):
                        found_citations.append(" ".join(match))

                    for match in re.findall(canlii_citation_pattern, clean_para):
                        # Format as "YYYY CanLII #### (CODE)"
                        found_citations.append(f"{match[0]} CanLII {match[1]} ({match[2]})")
                    
                    if found_citations:
                        for citation in found_citations:
                            if citation not in matches_by_citation:
                                matches_by_citation[citation] = []
                            matches_by_citation[citation].append({
                                "paragraph": clean_para,
                                "page": page_number,
                                "pinpoints": _extract_pinpoints(clean_para),
                            })
    except Exception as e:
        print(f"Error reading {filename}: {e}")
        return []

    results_list = [
        {
            "citation": citation,
            "citation_normalized": _normalize_citation(citation),
            "instances": instances,
        }
        for citation, instances in matches_by_citation.items()
    ]
    total_citations = sum(len(item["instances"]) for item in results_list)
    # Generate a concise AI summary for the file
    file_summary = ""
    if full_text_parts:
        full_text = "\n\n".join(full_text_parts)
        max_chars = int(os.environ.get("SUMMARY_MAX_CHARS", "60000"))
        chunk_chars = int(os.environ.get("SUMMARY_CHUNK_CHARS", "12000"))
        if len(full_text) <= max_chars:
            prompt = (
                "Summarize the following legal document into five sentences. "
                "Keep it concise and factual.\n\n"
                f"{full_text}"
            )
            try:
                file_summary = run_prompt(prompt).strip()
            except Exception as e:
                print(f"Error summarizing {filename}: {e}")
        else:
            # Chunk long documents to avoid context length errors
            chunks = [full_text[i:i + chunk_chars] for i in range(0, len(full_text), chunk_chars)]
            chunk_summaries = []
            for i, chunk in enumerate(chunks):
                prompt = (
                    "Summarize the following legal document chunk into three sentences. "
                    "Keep it concise and factual.\n\n"
                    f"{chunk}"
                )
                try:
                    chunk_summaries.append(run_prompt(prompt).strip())
                except Exception as e:
                    print(f"Error summarizing chunk {i+1} of {filename}: {e}")
            if chunk_summaries:
                final_prompt = (
                    "Combine the following summaries into five sentences total. "
                    "Keep it concise and factual.\n\n"
                    + "\n\n".join(chunk_summaries)
                )
                try:
                    file_summary = run_prompt(final_prompt).strip()
                except Exception as e:
                    print(f"Error summarizing {filename}: {e}")

    output_payload = {
        "filename": filename,
        # Optional metadata from file_info_mapper CSVs
        "court_no": (file_info_row.get("COURT_NO") or "").strip(),
        "style_of_cause": (file_info_row.get("STYLE_OF_CAUSE") or "").strip(),
        "english_nature_desc": (file_info_row.get("ENGLISH_NATURE_DESC") or "").strip(),
        "english_track_name": (file_info_row.get("ENGLISH_TRACK_NAME") or "").strip(),
        "unique_citations": len(results_list),
        "total_citations": total_citations,
        "file_ai_summary": file_summary,
        "results": results_list,
    }

    # Write results for this file (overwrite if exists)
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error writing {output_path}: {e}")

    return output_payload

def batch_process_pdfs(file_paths):
    """
    A wrapper function that loops through a list of file paths 
    and aggregates all citation data into one master list.
    """
    # Ensure file_paths is a list even if a single string is passed
    if isinstance(file_paths, str):
        file_paths = [file_paths]
        
    master_results = []
    
    for path in file_paths:
        file_results = process_single_pdf(path)
        if file_results:
            master_results.append(file_results)
        
    return master_results
