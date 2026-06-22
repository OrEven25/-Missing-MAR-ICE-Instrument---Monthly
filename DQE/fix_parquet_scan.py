"""Replace all parquet_scan('{var}') patterns in f-strings with {parquet_scan_expr(var)}"""
import re, pathlib

FILES = [
    r"C:\Users\or.even\DQE\dashboard\pages\0_Overview.py",
    r"C:\Users\or.even\DQE\dashboard\pages\1_Data_Stats.py",
    r"C:\Users\or.even\DQE\dashboard\pages\2_Test_Cases.py",
]

# Match:  parquet_scan('{some_var}')   or   parquet_scan('{paths["orders"]}')
PATTERN = re.compile(r"""parquet_scan\('{([\w\[\]"]+)}'\)""")

for fpath in FILES:
    p = pathlib.Path(fpath)
    src = p.read_text(encoding="utf-8")
    new_src = PATTERN.sub(lambda m: f"{{parquet_scan_expr({m.group(1)})}}", src)
    if new_src != src:
        # Ensure import is present
        if "parquet_scan_expr" not in new_src.split("from utils import")[0] + "":
            new_src = new_src.replace(
                "from utils import",
                "from utils import parquet_scan_expr,\\\n    ",
                1
            )
            # tidy up if already comma-separated
            new_src = new_src.replace("parquet_scan_expr,\\\n    discover_all_data_files",
                                       "parquet_scan_expr, discover_all_data_files")
        p.write_text(new_src, encoding="utf-8")
        matches = len(PATTERN.findall(src))
        print(f"OK {p.name}: replaced {matches} occurrences")
    else:
        print(f"  {p.name}: no changes")
