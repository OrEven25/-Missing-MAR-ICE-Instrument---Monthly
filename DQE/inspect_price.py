import duckdb

p = "C:/Users/or.even/DQE/EPEX/extracted/2026-06-16/CLIENT_ORDERS_2026-06-16-14922e92-6d84-4747-93c1-da67523e107f.parquet"
pt = "C:/Users/or.even/DQE/EPEX/extracted/2026-06-16/CLIENT_TRADES_2026-06-16-2b190614-d242-47ae-b110-9657aad2b357.parquet"
ins = "EPEX_EL_DE_XBID_Hour_Power_21-22_XB_CH21"

print("=== ORDERS cols ===")
cols = duckdb.query(f"SELECT * FROM parquet_scan('{p}') LIMIT 0").df().columns.tolist()
print(cols)

print("\n=== ORDER_TYPE distinct ===")
print(duckdb.query(f"SELECT DISTINCT ORDER_TYPE FROM parquet_scan('{p}') WHERE INS_TYPE='{ins}' LIMIT 10").df())

print("\n=== Sample orders (price + direction) ===")
print(duckdb.query(f"""
    SELECT TRAN_DATETIME, ORDER_TYPE, PRICE, VOLUME, PARTY
    FROM parquet_scan('{p}')
    WHERE INS_TYPE='{ins}'
    ORDER BY TRAN_DATETIME LIMIT 10
""").df().to_string())

print("\n=== TRADES cols ===")
cols_t = duckdb.query(f"SELECT * FROM parquet_scan('{pt}') LIMIT 0").df().columns.tolist()
print(cols_t)

print("\n=== Sample trades ===")
print(duckdb.query(f"""
    SELECT TRAN_DATETIME, BUY_SELL, PRICE, VOLUME, PARTY
    FROM parquet_scan('{pt}')
    WHERE INS_TYPE='{ins}'
    ORDER BY TRAN_DATETIME LIMIT 10
""").df().to_string())
