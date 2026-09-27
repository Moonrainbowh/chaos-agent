# -*- coding: utf-8 -*-
"""
Generate publication-grade comparative analysis charts for brine concentration data.
Target: 25°C vs 60°C across time, concentration (g/L), and mass fraction (%).
"""
import os
import openpyxl
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import MultipleLocator, AutoMinorLocator

# Set up Chinese and clean font rendering
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans', 'Arial']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 300

excel_path = r"C:\Users\Windows11\Desktop\0922-出卤浓度.xlsx"
desktop = r"C:\Users\Windows11\Desktop"

wb = openpyxl.load_workbook(excel_path, data_only=True)

# Load 60度
ws60 = wb["60度"]
data60 = []
for r in range(2, ws60.max_row + 1):
    vals = [ws60.cell(r, c).value for c in range(1, 4)]
    if vals[0] is not None and vals[1] is not None and vals[2] is not None:
        data60.append([float(vals[0]), float(vals[1]), float(vals[2])])
df60 = pd.DataFrame(data60, columns=["time", "mass_fraction", "concentration"]).sort_values("time")

# Load 25度
ws25 = wb["25度"]
data25 = []
for r in range(1, ws25.max_row + 1):
    vals = [ws25.cell(r, c).value for c in range(1, 4)]
    if vals[0] is not None and vals[1] is not None and vals[2] is not None:
        try:
            data25.append([float(vals[0]), float(vals[1]), float(vals[2])])
        except (ValueError, TypeError):
            continue
df25 = pd.DataFrame(data25, columns=["time", "mass_fraction", "concentration"]).sort_values("time")

print(f"Loaded 60°C rows: {len(df60)}, 25°C rows: {len(df25)}")

# Separate operational phase (t > 0)
df60_op = df60[df60["time"] > 0].copy()
df25_op = df25[df25["time"] > 0].copy()

# ==============================================================================
# Chart 1: 双纵轴时间对比大图 (出卤浓度与质量分数_双纵轴时间对比大图.png)
# ==============================================================================
fig, ax1 = plt.subplots(figsize=(13, 7.5), constrained_layout=True)
ax2 = ax1.twinx()

c_25_conc = "#1f77b4"      # 蓝
c_60_conc = "#d62728"      # 红
c_25_mass = "#38bdf8"      # 天蓝
c_60_mass = "#f97316"      # 橙

# Left axis: 出卤浓度 (g/L)
l1, = ax1.plot(df60["time"], df60["concentration"], color=c_60_conc, marker='o', markersize=6,
               linewidth=2.4, label="60℃ 出卤浓度 (g/L)")
l2, = ax1.plot(df25["time"], df25["concentration"], color=c_25_conc, marker='s', markersize=6,
               linewidth=2.4, label="25℃ 出卤浓度 (g/L)")

# Right axis: 质量分数 (%)
l3, = ax2.plot(df60["time"], df60["mass_fraction"], color=c_60_mass, marker='^', markersize=5,
               linewidth=1.8, linestyle='--', label="60℃ 质量分数 (%)")
l4, = ax2.plot(df25["time"], df25["mass_fraction"], color=c_25_mass, marker='v', markersize=5,
               linewidth=1.8, linestyle='--', label="25℃ 质量分数 (%)")

# Axes formatting
ax1.set_xlabel("标准时间 (min)", fontsize=14, fontweight='bold', labelpad=10)
ax1.set_ylabel("出卤浓度 (g/L)", fontsize=14, fontweight='bold', color="#111827", labelpad=10)
ax2.set_ylabel("质量分数 (%)", fontsize=14, fontweight='bold', color="#374151", labelpad=10)

ax1.tick_params(axis='x', labelsize=12)
ax1.tick_params(axis='y', labelsize=12, labelcolor="#111827")
ax2.tick_params(axis='y', labelsize=12, labelcolor="#374151")

ax1.set_xlim(-10, 500)
ax1.set_ylim(-10, 340)
ax2.set_ylim(-1, 30)

# Secondary axis grid & primary grid
ax1.grid(True, linestyle=':', alpha=0.6, color='#94a3b8')
ax1.xaxis.set_major_locator(MultipleLocator(50))
ax1.xaxis.set_minor_locator(AutoMinorLocator(5))

# Combine legends
lines = [l1, l2, l3, l4]
labels = [l.get_label() for l in lines]
legend = ax1.legend(lines, labels, loc='lower right', frameon=True, framealpha=0.92,
                    facecolor='#ffffff', edgecolor='#cbd5e1', fontsize=11, shadow=True)

# Key annotations
max60_row = df60_op.loc[df60_op["concentration"].idxmax()]
ax1.annotate(f"60℃ 峰值: {max60_row['concentration']:.1f} g/L ({max60_row['mass_fraction']:.1f}%)\n[t={int(max60_row['time'])} min]",
             xy=(max60_row['time'], max60_row['concentration']),
             xytext=(max60_row['time'] - 70, max60_row['concentration'] + 15),
             arrowprops=dict(arrowstyle="->", color=c_60_conc, lw=1.5),
             fontsize=10, fontweight='bold', color=c_60_conc,
             bbox=dict(boxstyle="round,pad=0.3", fc="#fef2f2", ec=c_60_conc, lw=1))

max25_row = df25_op.loc[df25_op["concentration"].idxmax()]
ax1.annotate(f"25℃ 峰值: {max25_row['concentration']:.1f} g/L ({max25_row['mass_fraction']:.1f}%)\n[t={int(max25_row['time'])} min]",
             xy=(max25_row['time'], max25_row['concentration']),
             xytext=(max25_row['time'] + 20, max25_row['concentration'] - 25),
             arrowprops=dict(arrowstyle="->", color=c_25_conc, lw=1.5),
             fontsize=10, fontweight='bold', color=c_25_conc,
             bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec=c_25_conc, lw=1))

plt.title("出卤过程时间动力学演变与温度对比大图 (25℃ vs 60℃)", fontsize=16, fontweight='bold', pad=16)
out_dual = os.path.join(desktop, "出卤浓度与质量分数_双纵轴时间对比大图.png")
plt.savefig(out_dual, dpi=300)
plt.close(fig)
print(f"Chart 1 saved: {out_dual}")

# ==============================================================================
# Chart 2: 出卤浓度综合对比分析大图 (出卤浓度综合对比分析大图.png)
# ==============================================================================
fig = plt.figure(figsize=(16, 11), constrained_layout=True)
gs = gridspec.GridSpec(2, 2, figure=fig, height_ratios=[1.1, 0.9])

ax_a = fig.add_subplot(gs[0, 0])
ax_b = fig.add_subplot(gs[0, 1])
ax_c = fig.add_subplot(gs[1, 0])
ax_d = fig.add_subplot(gs[1, 1])

# Panel A: 出卤浓度对比与稳态参考线
ax_a.plot(df60_op["time"], df60_op["concentration"], color="#dc2626", marker='o', markersize=5,
          linewidth=2.2, label="60℃ 出卤浓度")
ax_a.plot(df25_op["time"], df25_op["concentration"], color="#2563eb", marker='s', markersize=5,
          linewidth=2.2, label="25℃ 出卤浓度")

mean60_c = df60_op["concentration"].mean()
mean25_c = df25_op["concentration"].mean()
ax_a.axhline(mean60_c, color="#dc2626", linestyle=":", alpha=0.8, label=f"60℃ 均值: {mean60_c:.1f} g/L")
ax_a.axhline(mean25_c, color="#2563eb", linestyle=":", alpha=0.8, label=f"25℃ 均值: {mean25_c:.1f} g/L")

ax_a.set_title("(A) 25℃ 与 60℃ 出卤浓度随时间动力学对比", fontsize=13, fontweight='bold')
ax_a.set_xlabel("标准时间 (min)", fontsize=11)
ax_a.set_ylabel("出卤浓度 (g/L)", fontsize=11)
ax_a.grid(True, linestyle=":", alpha=0.6)
ax_a.legend(loc="lower right", fontsize=9.5, frameon=True)
ax_a.set_ylim(270, 320)

# Panel B: 质量分数对比与演变
ax_b.plot(df60_op["time"], df60_op["mass_fraction"], color="#ea580c", marker='^', markersize=5,
          linewidth=2.0, label="60℃ 质量分数")
ax_b.plot(df25_op["time"], df25_op["mass_fraction"], color="#0284c7", marker='v', markersize=5,
          linewidth=2.0, label="25℃ 质量分数")

mean60_w = df60_op["mass_fraction"].mean()
mean25_w = df25_op["mass_fraction"].mean()
ax_b.axhline(mean60_w, color="#ea580c", linestyle=":", alpha=0.8, label=f"60℃ 均值: {mean60_w:.2f}%")
ax_b.axhline(mean25_w, color="#0284c7", linestyle=":", alpha=0.8, label=f"25℃ 均值: {mean25_w:.2f}%")

ax_b.set_title("(B) 25℃ 与 60℃ 质量分数随时间演变对比", fontsize=13, fontweight='bold')
ax_b.set_xlabel("标准时间 (min)", fontsize=11)
ax_b.set_ylabel("质量分数 (%)", fontsize=11)
ax_b.grid(True, linestyle=":", alpha=0.6)
ax_b.legend(loc="lower right", fontsize=9.5, frameon=True)
ax_b.set_ylim(23, 27)

# Panel C: 质量分数与出卤浓度关联机理拟合
ax_c.scatter(df60_op["mass_fraction"], df60_op["concentration"], color="#dc2626", s=45, alpha=0.85,
             edgecolors='w', label="60℃ 测点")
ax_c.scatter(df25_op["mass_fraction"], df25_op["concentration"], color="#2563eb", s=45, alpha=0.85,
             edgecolors='w', label="25℃ 测点")

p60 = np.polyfit(df60_op["mass_fraction"], df60_op["concentration"], 1)
p25 = np.polyfit(df25_op["mass_fraction"], df25_op["concentration"], 1)
x_fit = np.linspace(23.5, 26.5, 50)
ax_c.plot(x_fit, np.polyval(p60, x_fit), color="#b91c1c", linestyle="-", lw=1.8,
          label=f"60℃ 线性拟合 (斜率={p60[0]:.2f})")
ax_c.plot(x_fit, np.polyval(p25, x_fit), color="#1d4ed8", linestyle="-", lw=1.8,
          label=f"25℃ 线性拟合 (斜率={p25[0]:.2f})")

ax_c.set_title("(C) 质量分数与出卤浓度物理相关性分析", fontsize=13, fontweight='bold')
ax_c.set_xlabel("质量分数 (%)", fontsize=11)
ax_c.set_ylabel("出卤浓度 (g/L)", fontsize=11)
ax_c.grid(True, linestyle=":", alpha=0.6)
ax_c.legend(loc="upper left", fontsize=9.5, frameon=True)

# Panel D: 稳定阶段分布箱线图与核心 KPI 对比
box_data_c = [df25_op["concentration"], df60_op["concentration"]]
box_data_w = [df25_op["mass_fraction"], df60_op["mass_fraction"]]

ax_d.axis('off')
table_data = [
    ["统计指标", "25℃ 运行相", "60℃ 运行相", "工程与科学对比解析"],
    ["平均出卤浓度", f"{mean25_c:.2f} g/L", f"{mean60_c:.2f} g/L", f"60℃ 相比 25℃ 提高 {mean60_c - mean25_c:+.2f} g/L (+{(mean60_c/mean25_c - 1)*100:.1f}%)"],
    ["出卤浓度峰值", f"{df25_op['concentration'].max():.1f} g/L", f"{df60_op['concentration'].max():.1f} g/L", "高温显著加快溶解释放速率，快速达到高位"],
    ["平均质量分数", f"{mean25_w:.2f}%", f"{mean60_w:.2f}%", f"60℃ 质量分数平均高 {mean60_w - mean25_w:+.2f}%"],
    ["浓度极差(波动度)", f"{df25_op['concentration'].max() - df25_op['concentration'].min():.1f} g/L",
                       f"{df60_op['concentration'].max() - df60_op['concentration'].min():.1f} g/L", "25℃ 波动达 26.3 g/L，60℃ 波动收敛在 20.9 g/L"],
    ["溶解释放响应", "13.3 min 达 283.5 g/L", "15.0 min 达 297.2 g/L", "60℃ 首个取样点即接近 300 g/L，热力学驱动力强"],
    ["稳态阶段表现", "200min后出现多次波动", "120-240min维持 >308 g/L", "60℃ 具有更平稳的出卤平台期与更高的品位"]
]

table = ax_d.table(cellText=table_data, loc="center", cellLoc="center",
                   colWidths=[0.19, 0.22, 0.22, 0.37])
table.auto_set_font_size(False)
table.set_fontsize(9.5)
table.scale(1.0, 1.7)

for (row, col), cell in table.get_celld().items():
    cell.set_edgecolor("#cbd5e1")
    if row == 0:
        cell.set_facecolor("#1e293b")
        cell.get_text().set_color("#f8fafc")
        cell.get_text().set_fontweight("bold")
    elif row % 2 == 1:
        cell.set_facecolor("#f8fafc")
    else:
        cell.set_facecolor("#ffffff")
ax_d.set_title("(D) 核心工艺运行性能指标 (KPI) 综合对比", fontsize=13, fontweight='bold', pad=10)

out_comp = os.path.join(desktop, "出卤浓度综合对比分析大图.png")
plt.savefig(out_comp, dpi=300)
plt.close(fig)
print(f"Chart 2 saved: {out_comp}")
