"""Shared utilities for the CubeLogic DQE Dashboard."""
import json
import re
from pathlib import Path

BASE_DIR = Path(r"C:\Users\or.even\DQE")

# Registry of exchanges: name → root folder of extracted parquet files.
# Files may live directly in the folder (Nordpool style) or in date sub-dirs (EPEX style).
# Add new exchanges here as they are onboarded.
EXCHANGE_DIRS: dict[str, Path] = {
    "Nordpool": BASE_DIR / "Nordpool" / "extracted",
    "EPEX":     BASE_DIR / "EPEX"     / "extracted",
    "OMIE":     BASE_DIR / "OMIE"     / "extracted",
}


def parquet_scan_expr(paths: "str | list[str]") -> str:
    """
    Build a DuckDB parquet_scan() expression that handles one or multiple files.
    Single path  → parquet_scan('path')
    Multiple paths → parquet_scan(['p1','p2',...])
    """
    if isinstance(paths, str):
        return f"parquet_scan('{paths}')"
    if len(paths) == 1:
        return f"parquet_scan('{paths[0]}')"
    # DuckDB accepts a JSON-style list literal for multi-file scans
    list_literal = json.dumps(paths)
    return f"parquet_scan({list_literal})"


def _scan_folder(folder: Path) -> dict:
    """
    Scan a folder for CLIENT_ORDERS / CLIENT_TRADES parquet files.
    Supports two layouts:
      • Flat:  folder/CLIENT_ORDERS_YYYY-MM-DD-<uuid>.parquet
      • Dated: folder/YYYY-MM-DD/CLIENT_ORDERS_<uuid>.parquet

    Multiple files for the same date/type are collected into a list so that
    DuckDB can union-scan them with parquet_scan([...]).

    Returns {date_str: {orders: path_or_list, trades: path_or_list}} for complete pairs only.
    """
    dates: dict = {}

    def _add(d: str, key: str, path: Path):
        dates.setdefault(d, {"orders": [], "trades": []})
        dates[d][key].append(str(path).replace("\\", "/"))

    # Flat layout — date embedded in filename
    for f in folder.glob("CLIENT_*.parquet"):
        if "CROSS" in f.name:
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        if not m:
            continue
        d = m.group(1)
        if "CLIENT_ORDERS" in f.name:
            _add(d, "orders", f)
        elif "CLIENT_TRADES_" in f.name:
            _add(d, "trades", f)

    # Dated sub-directory layout — folder/YYYY-MM-DD/*.parquet
    for sub in folder.iterdir():
        if not (sub.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sub.name)):
            continue
        d = sub.name
        for f in sub.glob("CLIENT_*.parquet"):
            if "CROSS" in f.name:
                continue
            if "CLIENT_ORDERS" in f.name:
                _add(d, "orders", f)
            elif "CLIENT_TRADES_" in f.name:
                _add(d, "trades", f)

    result = {}
    for d, v in sorted(dates.items(), reverse=True):
        if v["orders"] and v["trades"]:
            # Flatten single-item lists to plain strings for backward compat
            result[d] = {
                "orders": v["orders"][0] if len(v["orders"]) == 1 else v["orders"],
                "trades": v["trades"][0] if len(v["trades"]) == 1 else v["trades"],
            }
    return result


def discover_all_data_files() -> dict:
    """
    Discover parquet files for all registered exchanges.
    Returns: {exchange_name: {date_str: {"orders": path, "trades": path}}}
    Only exchanges with at least one complete date pair are included.
    """
    result = {}
    for exchange, folder in EXCHANGE_DIRS.items():
        if not folder.exists():
            continue
        dates = _scan_folder(folder)
        if dates:
            result[exchange] = dates
    return result


def discover_data_files() -> dict:
    """Legacy helper — returns Nordpool files only. {date: {orders, trades}}"""
    return discover_all_data_files().get("Nordpool", {})
