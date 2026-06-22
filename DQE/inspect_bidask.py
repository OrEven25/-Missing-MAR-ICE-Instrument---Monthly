import duckdb
p = "C:/Users/or.even/DQE/EPEX/extracted/2026-06-16/CLIENT_ORDERS_2026-06-16-14922e92-6d84-4747-93c1-da67523e107f.parquet"
ins = "EPEX_EL_DE_XBID_Hour_Power_21-22_XB_CH21"
print(duckdb.query(f"SELECT DISTINCT BID_ASK, COUNT(*) n FROM parquet_scan('{p}') WHERE INS_TYPE='{ins}' GROUP BY BID_ASK").df())
