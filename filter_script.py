import pandas as pd

# 读取Excel文件
input_file = "outputs/bench_compare_20260216/qwen3max/workfiles/SpreadsheetBench 自动抽样套件（10题）/sb_24_23/1_24-23_init.xlsx"
output_file = "outputs/bench_compare_20260216/qwen3max/workfiles/SpreadsheetBench 自动抽样套件（10题）/sb_24_23/filtered_result.xlsx"

# 读取数据
df = pd.read_excel(input_file)

# 显示原始数据信息
print(f"原始数据行数: {len(df)}")
print(f"原始数据中各Group值的分布:")
print(df['Group'].value_counts())

# 过滤掉Group为'@9T'、'SAL'或'T9A'的行
excluded_groups = ['@9T', 'SAL', 'T9A']
filtered_df = df[~df['Group'].isin(excluded_groups)]

# 显示过滤后的数据信息
print(f"\n过滤后数据行数: {len(filtered_df)}")
print(f"过滤后各Group值的分布:")
print(filtered_df['Group'].value_counts())

# 保存结果到新文件
filtered_df.to_excel(output_file, index=False)
print(f"\n结果已保存到: {output_file}")