---
name: loss-analysis
description: 对 Jupyter Notebook 的训练损失曲线进行可复现统计与收敛分析。适用于 GD、SGD、ADN 等优化器的 epoch-loss 比较。
metadata:
  version: "0.1.0"
---
# Loss curve analysis — Analysis 专用

## 数据优先级

1. 先确认 Notebook `code` cell 的 `outputs` 与 `execution_count`，区分实际执行输出和 Markdown 的 Expected Output 示例。
2. 如果没有文件读取权限或没有收到数据数组，必须说明无法完成计算，不应构造 loss 数列。
3. 如果只有 Expected Output，标记“示例数据”；不得声称实验实际执行成功。
4. 对提取出的两组数值使用 `skills:loss_metrics` 执行确定性统计，不要只依赖 LLM 心算。
5. 核对样本数是否一致、是否单调下降；“平均每个 epoch 的下降”采用首末值差除以 (N - 1) 个区间。
6. 只有当损失曲线计算输出足以支持时，才比较最终 loss、下降百分比和收敛速率；不要将线性外推称作真实预测。

## 输出格式

- 数据来源：实际执行 / Markdown 示例 / 无法验证
- 样本数量和优化器名称
- 均值、样本标准差、首尾、总降幅、平均降幅、单调性
- 两组差异及统计解释
- 计算限制、未经验证项
