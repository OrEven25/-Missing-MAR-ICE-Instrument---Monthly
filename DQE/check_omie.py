import duckdb, os

base = r"C:\Users\or.even\DQE\OMIE\extracted"

for root, dirs, files in os.walk(base):
    for f in sorted(files):
        if not f.endswith(".parquet"):
            continue
        path = os.path.join(root, f).replace("\\", "/")
        try:
            n = duckdb.query(f"SELECT COUNT(*) AS n FROM parquet_scan('{path}')").df().iloc[0]["n"]
            flag = "  *** EMPTY ***" if n == 0 else ""
            rel = os.path.relpath(path, base)
            print(f"{n:>8,}  {rel}{flag}")
        except Exception as e:
            print(f"   ERROR  {f}: {e}")
