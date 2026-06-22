import duckdb

checks = [
    ("Nordpool", "C:/Users/or.even/DQE/Nordpool/extracted/CLIENT_ORDERS_2026-06-16-c574f5d4-1468-492c-9a62-e8ac74fe14f1.parquet",
     "NORDPOOL_EL_SE_SE4_XBID_Quarter_Hour_Power_CQ20q4"),
    ("EPEX",     "C:/Users/or.even/DQE/EPEX/extracted/2026-06-16/CLIENT_ORDERS_2026-06-16-14922e92-6d84-4747-93c1-da67523e107f.parquet",
     "EPEX_EL_DE_XBID_Hour_Power_21-22_XB_CH21"),
    ("OMIE",     "C:/Users/or.even/DQE/OMIE/extracted/CLIENT_ORDERS_2026-06-16-3e22a168-0051-40ef-8593-ceb6d2772cc4.parquet",
     "VOLUE_OMIE_EL_ES_CQ22q1"),
]

for name, path, ins in checks:
    print(f"=== {name} | {ins} ===")
    df = duckdb.query(f"""
        SELECT DISTINCT DELIVERY_PERIOD,
               MIN(TRAN_DATETIME) AS first_order,
               MAX(TRAN_DATETIME) AS last_order
        FROM parquet_scan('{path}')
        WHERE INS_TYPE = '{ins}'
        GROUP BY DELIVERY_PERIOD
        ORDER BY DELIVERY_PERIOD
    """).df()
    print(df.to_string())
    print()
