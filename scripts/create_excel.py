import pandas as pd
from pathlib import Path

# 创建示例数据
data = {
    '姓名': ['张三', '李四', '王五', '赵六', '钱七'],
    '年龄': [25, 30, 28, 35, 27],
    '部门': ['销售部', '技术部', '财务部', '人事部', '市场部'],
    '工资': [8000, 12000, 9000, 7500, 8500],
    '入职日期': ['2020-01-15', '2019-06-20', '2021-03-10', '2018-11-05', '2020-08-25']
}

# 创建DataFrame
df = pd.DataFrame(data)

# 确保输出目录存在
output_dir = Path('outputs')
output_dir.mkdir(exist_ok=True)

# 写入Excel文件
output_file = output_dir / '员工信息表.xlsx'
with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='员工信息', index=False)

print(f'✅ Excel文件创建成功！')
print(f'📁 文件路径: {output_file}')
print(f'📊 包含 {len(df)} 行数据，{len(df.columns)} 列')
print(f'\n预览数据：')
print(df.to_string(index=False))
