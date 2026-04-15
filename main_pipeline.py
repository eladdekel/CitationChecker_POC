#!/usr/bin/env python3
"""
Main entry point for the Legal Citation Review pipeline.

Examples:
    # Process a single PDF
    python main_pipeline.py --file "pdfs/my_document.pdf"

    # Process every PDF in a folder
    python main_pipeline.py --folder "pdfs/"

    # Skip files that already have output JSONs
    python main_pipeline.py --folder "pdfs/" --skip-existing

    # Store results in Postgres instead of local JSON files
    python main_pipeline.py --folder "pdfs/" --storage DB
"""

import argparse
import os
import sys

from combined import run_pipeline


def main():
    parser = argparse.ArgumentParser(
        description="Run the Legal Citation Review pipeline on one PDF or a folder of PDFs.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Path to a single PDF to process")
    group.add_argument("--folder", help="Path to a folder of PDFs to process")

    parser.add_argument(
        "--storage",
        choices=["LOCAL", "DB"],
        default="LOCAL",
        help="Where to store results (default: LOCAL — writes to output_jsons/).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip PDFs that already have output JSONs.",
    )
    parser.add_argument(
        "--pdf-workers",
        type=int,
        default=2,
        help="Parallel PDF workers (default: 2).",
    )
    parser.add_argument(
        "--gpt-workers",
        type=int,
        default=6,
        help="Parallel GPT workers per PDF (default: 6).",
    )

    args = parser.parse_args()

    if args.file:
        if not os.path.isfile(args.file):
            sys.exit(f"File not found: {args.file}")
        run_pipeline(
            "SINGLE",
            args.file,
            storage_mode=args.storage,
            pdf_max_workers=args.pdf_workers,
            gpt_max_workers=args.gpt_workers,
            skip_duplicate=args.skip_existing,
        )
    else:
        if not os.path.isdir(args.folder):
            sys.exit(f"Folder not found: {args.folder}")
        run_pipeline(
            "MANY",
            args.folder,
            storage_mode=args.storage,
            pdf_max_workers=args.pdf_workers,
            gpt_max_workers=args.gpt_workers,
            skip_duplicate=args.skip_existing,
        )


if __name__ == "__main__":
    main()
