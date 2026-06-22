import duckdb

paths = [
    ("Nordpool", "C:/Users/or.even/DQE/Nordpool/extracted/CLIENT_ORDERS_2026-06-16-c574f5d4-1468-492c-9a62-e8ac74fe14f1.parquet"),
    ("EPEX",     "C:/Users/or.even/DQE/EPEX/extracted/2026-06-16/CLIENT_ORDERS_2026-06-16-14922e92-6d84-4747-93c1-da67523e107f.parquet"),
    ("OMIE",     "C:/Users/or.even/DQE/OMIE/extracted/CLIENT_ORDERS_2026-06-16-3e22a168-0051-40ef-8593-ceb6d2772cc4.parquet"),
]

for name, p in paths:
    print(f"=== {name} ===")
    cols = duckdb.query(f"SELECT * FROM parquet_scan('{p}') LIMIT 0").df().columns.tolist()
    relevant = [c for c in cols if any(x in c for x in ["DATETIME", "PERIOD", "INS_TYPE", "DELIVERY", "HOURS"])]
    print("Relevant cols:", relevant)
    df = duckdb.query(f"""
        SELECT TRAN_DATETIME, DELIVERY_PERIOD, INS_TYPE
        FROM parquet_scan('{p}')
        WHERE DELIVERY_PERIOD IS NOT NULL
        LIMIT 8
    """).df()
    print(df.to_string())
    print()
    # Also check distinct INS_TYPEs
    ins = duckdb.query(f"SELECT DISTINCT INS_TYPE, COUNT(*) as n FROM parquet_scan('{p}') GROUP BY INS_TYPE ORDER BY n DESC LIMIT 10").df()
    print("INS_TYPE distribution:")
    print(ins.to_string())
    print()
