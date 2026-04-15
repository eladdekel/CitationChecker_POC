# Legal Citation Review Tool

An AI-powered assistant that reads legal PDFs, extracts every citation, and flags ones that look suspicious — wrong pinpoint, wrong jurisdiction, fabricated case, or a mismatch between the cited case and the argument it's supporting.

This project is a working demonstration of how AI can be used responsibly inside a law firm: it does the tedious cite-checking work, then hands a short report to a human who decides what to act on.

---

## Table of Contents

- [Legal Citation Review Tool](#legal-citation-review-tool)
  - [Table of Contents](#table-of-contents)
  - [1. What the Tool Does](#1-what-the-tool-does)
  - [2. Setup](#2-setup)
    - [Requirements](#requirements)
    - [Install](#install)
    - [API keys](#api-keys)
    - [Optional: per-PDF metadata CSVs](#optional-per-pdf-metadata-csvs)
  - [3. Running the Pipeline](#3-running-the-pipeline)
    - [Process one PDF](#process-one-pdf)
    - [Process a folder of PDFs](#process-a-folder-of-pdfs)
    - [Skip PDFs already processed](#skip-pdfs-already-processed)
    - [Store output in Postgres instead of local files](#store-output-in-postgres-instead-of-local-files)
  - [4. Reading the Output and Flags](#4-reading-the-output-and-flags)
    - [Output shape](#output-shape)
    - [Where the flags live](#where-the-flags-live)
    - [Quick triage workflow](#quick-triage-workflow)
  - [5. Optional: Web Review Interface](#5-optional-web-review-interface)
  - [6. Optional: Pass 2 Deep-Dive Batch](#6-optional-pass-2-deep-dive-batch)
  - [7. Project Layout](#7-project-layout)
  - [Notes for the reviewer](#notes-for-the-reviewer)

---

## 1. What the Tool Does

The pipeline runs each PDF through eight steps:

| # | Step | Purpose |
|---|------|---------|
| 1 | Load metadata | Pull court file number, style of cause, and court from CSVs in [file_info_mapper/](file_info_mapper/) so later checks have context. (Optional — see [§2](#optional-per-pdf-metadata-csvs).) |
| 2 | Extract citations | Scan the PDF with regex for citation patterns (e.g. `2014 FC 1247`, `[2002] 1 S.C.R. 45`) and capture pinpoints (`para 15`, `pp 12-13`). |
| 3 | AI summary | Send the full document text to GPT for a five-sentence summary, which gives downstream AI calls context. |
| 4 | Verify on CanLII | Hit the CanLII API for each citation. Anything not found is flagged immediately. |
| 5 | Pull full case text | Fetch the cited case's full text from the `a2aj/canadian-case-law` Hugging Face dataset for deeper checks. |
| 6 | Rule-based flags | Run pinpoint, self-citation, jurisdiction, and age checks (see [§4](#4-reading-the-output-and-flags)). |
| 7 | AI scoring (Pass 1) | GPT scores how well the citation supports the argument, using only metadata first to keep cost down. |
| 8 | Generate report | Write a per-PDF summary that highlights low-scoring citations and totals each flag type. |

A second, optional **Pass 2** stage re-checks anything Pass 1 flagged as risky, this time with the full case text loaded into the prompt. See [§6](#6-optional-pass-2-deep-dive-batch).

---

## 2. Setup

### Requirements

- Python 3.10+
- (Optional) Node.js 16+ for the web review UI
- (Optional) PostgreSQL if you want to use `--storage DB`

### Install

```bash
# 1. Clone or unzip the project, then enter it
cd CitationChecker_POC

# 2. (Recommended) create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt
```

### API keys

Copy the template and fill in your keys:

```bash
cp .env.example .env
# then edit .env in your editor of choice
```

At minimum you need:

- `CANLII_KEY` — free key from [canlii.org/en/info/api](https://www.canlii.org/en/info/api.html)
- `OPENAI_API_KEY` — your OpenAI API key (`sk-...`). Used for the AI summary, Pass 1 scoring, and Pass 2 deep-dive.
- `OPENAI_MODEL` — *(optional)* model name. Defaults to `gpt-5-nano`; set to `gpt-5`, `gpt-4o`, or another chat model if you prefer.

The Postgres and Gemini keys are only needed for the optional features in §5 and §6.

### Optional: per-PDF metadata CSVs

The pipeline can enrich each PDF with metadata (court file number, case name, nature of proceeding, etc.) by looking it up in CSV files placed inside [file_info_mapper/](file_info_mapper/). This is **optional** — the pipeline runs fine without it, those fields just stay blank in the output.

**How the lookup works:** every CSV in `file_info_mapper/` is loaded at startup, and rows are keyed on the `FOREMOST_NUMBER` column. For each PDF, the pipeline strips the `.pdf` extension and looks up the resulting string. So a PDF named `4252139.pdf` will match the row whose `FOREMOST_NUMBER` is `4252139`.

**Required CSV columns** (header row must match exactly):

| Column | Meaning | Example |
|--------|---------|---------|
| `FOREMOST_NUMBER` | Lookup key — must equal the PDF filename (without `.pdf`). | `4252139` |
| `COURT_NO` | Court file number. Used by the self-citation check. | `T-8-25` |
| `STYLE_OF_CAUSE` | Case name as it appears on the document. | `ACME CORP v. SMITH` |
| `ENGLISH_NATURE_DESC` | Nature of proceeding. | `Patented Medicines (NOC) Regulations [Actions]` |
| `ENGLISH_TRACK_NAME` | Procedural track. | `Actions` |

Extra columns are ignored. You can split the data across multiple CSVs (e.g. `2023.csv`, `2024.csv`) — they're all merged into a single lookup table.

**Minimal example** (`file_info_mapper/sample.csv`):

```csv
FOREMOST_NUMBER,COURT_NO,STYLE_OF_CAUSE,ENGLISH_NATURE_DESC,ENGLISH_TRACK_NAME
4252139,T-8-25,"ACME CORP v. SMITH",Patented Medicines (NOC) Regulations [Actions],Actions
```

If a PDF has no matching row, the metadata fields are simply left empty in the output JSON; the rest of the pipeline still runs.

---

## 3. Running the Pipeline

The single entry point is [main_pipeline.py](main_pipeline.py).

### Process one PDF

```bash
python main_pipeline.py --file "pdfs/my_document.pdf"
```

### Process a folder of PDFs

```bash
python main_pipeline.py --folder "pdfs/"
```

### Skip PDFs already processed

```bash
python main_pipeline.py --folder "pdfs/" --skip-existing
```

### Store output in Postgres instead of local files

```bash
python main_pipeline.py --folder "pdfs/" --storage DB
```

After a run you'll see a per-file summary printed to the console:

```
Processing: my_document.pdf
Citations total: 14
Citations unique: 9
Citations under 0.6: ['2018 ONCA 123', '2002 SCC 84']
```

…and the full results land in [output_jsons/](output_jsons/).

---

## 4. Reading the Output and Flags

Every PDF produces two files in `output_jsons/`:

- `<filename>.json` — full data: every citation, every instance, every score
- `<filename>_report.json` — short rollup of issue counts

### Output shape

The top of each `<filename>.json` looks like:

```json
{
  "filename": "my_document.pdf",
  "court_no": "T-8-25",
  "style_of_cause": "ACME CORP v. SMITH",
  "english_nature_desc": "Patented Medicines (NOC) Regulations [Actions]",
  "english_track_name": "Actions",
  "unique_citations": 9,
  "total_citations": 14,
  "file_ai_summary": "Five-sentence AI summary of the document …",
  "results": [
    {
      "citation": "2018 ONCA 123",
      "citation_normalized": "2018onca123",
      "instances": [ /* one entry per occurrence — see flags table below */ ],
      "canlii_api_response": { /* … */ },
      "hf_result": { /* full case text + metadata */ }
    }
  ]
}
```

### Where the flags live

Open any `<filename>.json` and look inside `results[*].instances[*]`. Each citation instance has the following flag fields:

| Field | Meaning |
|-------|---------|
| `pinpoint_validation` | Does the cited paragraph number actually exist in the case? `valid` / `invalid` / `not_found`. |
| `self_citation_docket_flag` | `true` if the citation refers to the same case as the document being analyzed. |
| `out_of_jurisdiction_flag` | `true` if the citation is from a different court system than the document's own court (SCC excluded). |
| `age_mismatch_flag` | `true` if the surrounding paragraph uses words like "recent" but the cited case is decades old. |
| `keyword_overlap` | A 0.0–1.0 Jaccard score between the document's topic and the cited case's CanLII keywords. |
| `gpt_relation_score` | AI's 0.0–1.0 score for how relevant the cited case is to the argument. |
| `gpt_pinpoint_score` | AI's 0.0–1.0 score for whether the cited paragraph actually supports the point. |
| `gpt_relation_reasoning` | One-paragraph explanation from the AI. |
| `gpt_reason_code` | Compact tag like `IRRELEVANT`, `WEAK_SUPPORT`, `WRONG_PINPOINT`, `OK`. |
| `gpt_below_threshold` | `true` if either GPT score is under 0.6 — your "look at this" signal. |
| `gpt_pass` | Which AI pass produced this result (`1` or `2`). |

### Quick triage workflow

1. Open `<filename>_report.json` to see whether the document had any low-scoring citations.
2. If it did, open `<filename>.json` and search for `"gpt_below_threshold": true`.
3. Read the `paragraph` (the actual sentence in the legal document) alongside `gpt_relation_reasoning` (the AI's explanation) to decide whether the flag is real.

Anything not flagged is presumed fine — the tool's job is to *narrow* a lawyer's review, not replace it.

---

## 5. Optional: Web Review Interface

For a friendlier way to walk through flagged citations, the [citation_reviewer/](citation_reviewer/) folder contains a small Node.js dashboard that reads from Postgres and lets you mark each citation OK / Fraud / Ignore.

```bash
cd citation_reviewer
npm install
npm start
# open http://localhost:3000
```

It needs the same `.env` Postgres credentials and assumes you've run the pipeline with `--storage DB`. Full details in [citation_reviewer/README.md](citation_reviewer/README.md).

---

## 6. Optional: Pass 2 Deep-Dive Batch

Pass 1 only uses citation metadata — fast and cheap. Anything Pass 1 flags as risky can be sent through Pass 2, where the AI is given the full text of the cited case and re-scores the citation.

To save money, Pass 2 uses the OpenAI / Gemini Batch APIs (cheaper, asynchronous). Two helper scripts are provided:

```bash
python run_overnight.py          # OpenAI batches only
python run_dual_overnight.py     # OpenAI + Gemini in parallel
```

Both scripts loop until every flagged citation has a Pass 2 result, then update the JSONs/database in place.

---

## 7. Project Layout

```
CitationChecker_POC/
├── main_pipeline.py            # CLI entry point — start here
├── combined.py                 # Pipeline orchestration
├── parse_pdfs.py               # Step 2: PDF parsing & citation extraction
├── canlii_citation_history.py  # Step 4: CanLII enrichment
├── hf_citation_history.py      # Step 5: full case text from Hugging Face
├── analyze_gpt.py              # Steps 6–8: rule checks, GPT scoring, report
├── openai_connect.py           # Thin OpenAI client used for all AI calls
│
├── batch_gpt.py                # Pass 2 helpers (OpenAI Batch API)
├── batch_gemini.py             # Pass 2 helpers (Gemini Batch API)
├── generate_briefs.py          # Pre-summarise long cases for Pass 2
├── run_overnight.py            # Pass 2 driver (OpenAI)
├── run_dual_overnight.py       # Pass 2 driver (OpenAI + Gemini)
│
├── citation_reviewer/          # Node.js review dashboard (read-only-ish UI)
├── citation_triage/            # Lightweight alternative review UI
│
├── pdfs/                       # Drop input PDFs here
├── file_info_mapper/           # Optional CSVs supplying per-PDF metadata (see §2)
├── output_jsons/               # Pipeline output: per-PDF JSON + report
│
├── requirements.txt
└── .env.example                # Template — copy to .env and fill in
```

---

## Notes for the reviewer

- The tool is *augmentative*, not autonomous. Every flag is intended to be read by a human — the AI never decides on its own that a citation is wrong.
- The two-pass design is the main cost-management trick: cheap metadata-only scoring on every citation, expensive full-text scoring only on the small subset that looks risky.
- Output is fully reproducible: re-running the same PDF through the pipeline overwrites the same JSON, so you can tweak thresholds in [analyze_gpt.py](analyze_gpt.py) and re-check.

---

> *Portions of this tool were built with the assistance of Anthropic's Claude Opus 4.5 and Claude Opus 4.6.*
