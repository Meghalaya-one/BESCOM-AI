#!/usr/bin/env python3
"""
Load all sheets from an Excel file into a Neon (Postgres) database.

Setup:
    pip install pandas openpyxl sqlalchemy psycopg2-binary

    export NEON_DATABASE_URL="postgresql://<user>:<password>@<host>/<db>?sslmode=require&channel_binding=require"

Usage:
    python excel_to_neon.py --file data.xlsx
    python excel_to_neon.py --file data.xlsx --sheet "Sheet1"
    python excel_to_neon.py --file data.xlsx --if-exists append
"""

import argparse
import os
import re
import sys

import pandas as pd
from sqlalchemy import create_engine


def to_table_name(sheet_name: str) -> str:
    name = sheet_name.strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "sheet"
    if name[0].isdigit():
        name = f"t_{name}"
    return name


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [
        re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", str(c).strip().lower())).strip("_")
        or f"col_{i}"
        for i, c in enumerate(df.columns)
    ]
    return df


def main():
    parser = argparse.ArgumentParser(description="Load Excel data into Neon Postgres")
    parser.add_argument("--file", required=True, help="Path to the .xlsx file")
    parser.add_argument("--sheet", default=None, help="Specific sheet name (default: all sheets)")
    parser.add_argument("--schema", default="public", help="Target schema (default: public)")
    parser.add_argument(
        "--if-exists",
        choices=["replace", "append", "fail"],
        default="replace",
        help="Behavior if table already exists (default: replace)",
    )
    parser.add_argument(
        "--db-url",
        default=os.environ.get("NEON_DATABASE_URL"),
        help="Postgres connection string (default: NEON_DATABASE_URL env var)",
    )
    parser.add_argument("--chunksize", type=int, default=1000, help="Rows per insert batch")
    args = parser.parse_args()

    if not args.db_url:
        sys.exit(
            "No database URL provided. Set NEON_DATABASE_URL env var or pass --db-url."
        )

    if not os.path.exists(args.file):
        sys.exit(f"File not found: {args.file}")

    print(f"Reading {args.file} ...")
    sheets = pd.read_excel(
        args.file,
        sheet_name=args.sheet if args.sheet else None,
        engine="openpyxl",
    )

    if isinstance(sheets, pd.DataFrame):
        sheets = {args.sheet or "sheet1": sheets}

    engine = create_engine(args.db_url)

    with engine.connect() as conn:
        for sheet_name, df in sheets.items():
            if df.empty:
                print(f"Skipping empty sheet: {sheet_name}")
                continue

            table_name = to_table_name(sheet_name)
            df = clean_columns(df)
            df = df.where(pd.notnull(df), None)

            print(f"Loading sheet '{sheet_name}' -> table '{args.schema}.{table_name}' "
                  f"({len(df)} rows, {len(df.columns)} cols) ...")

            df.to_sql(
                table_name,
                con=conn,
                schema=args.schema,
                if_exists=args.if_exists,
                index=False,
                chunksize=args.chunksize,
                method="multi",
            )
            print(f"  Done: {table_name}")

    print("All sheets loaded successfully.")


if __name__ == "__main__":
    main()
