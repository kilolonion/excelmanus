from pathlib import Path
import json
import pandas as pd

input_path = Path("stress_test_comprehensive.xlsx")
output_csv = Path("outputs/city_sales_stats.csv")
output_json = Path("outputs/city_sales_stats_summary.json")

output_csv.parent.mkdir(parents=True, exist_ok=True)

required_columns = ["订单编号", "城市", "数量", "单价(元)", "状态"]

# 销售明细前两行是标题和说明，第三行为列头。
df = pd.read_excel(
    input_path,
    sheet_name="销售明细",
    header=2,
    usecols=required_columns,
)

df.columns = [str(c).strip() for c in df.columns]

missing = [c for c in required_columns if c not in df.columns]
if missing:
    raise ValueError(f"缺少必要列: {missing}")

status_keep = {"已完成", "待审核"}
df = df[df["状态"].isin(status_keep)].copy()

for col in ["数量", "单价(元)"]:
    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

# 按需求统一以 数量 × 单价 作为销售额。
df["销售额"] = df["数量"] * df["单价(元)"]

result = (
    df.groupby("城市", dropna=False)
    .agg(
        订单数=("订单编号", "count"),
        总销售额=("销售额", "sum"),
    )
    .reset_index()
    .sort_values("总销售额", ascending=False)
)

result["总销售额"] = result["总销售额"].round(2)
result.to_csv(output_csv, index=False, encoding="utf-8-sig")

summary = {
    "source_file": str(input_path),
    "sheet_name": "销售明细",
    "header_row": 2,
    "status_filter": sorted(list(status_keep)),
    "filtered_rows": int(len(df)),
    "city_count": int(result["城市"].nunique(dropna=True)),
    "output_csv": str(output_csv),
    "top10": result.head(10).to_dict(orient="records"),
}

output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"filtered_rows={summary['filtered_rows']}")
print(f"city_count={summary['city_count']}")
print(f"output_csv={output_csv}")
print(result.to_string(index=False))
