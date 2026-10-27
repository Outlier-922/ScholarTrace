# 绘图演示 CSV

synthetic_plotting_demo.csv 是人为构造的软件演示数据，**不是论文、模型基准或真实实验结果**。三个演示模型各有三条编号记录，用于检查上传、聚合、误差条和归一化。

| 列 | 演示含义 |
|---|---|
| model | 虚构对象标签 |
| run | 观测编号，不证明真实实验或独立性 |
| accuracy | 演示百分数，作图明确填 % |
| latency_ms | 演示延迟，单位 ms |
| parameters_million | 演示参数量，单位百万 |
| energy_j | 演示能耗，单位 J |

柱状：横轴 model、指标 accuracy、重复标识 run、均值聚合，可选 SD 或 SEM。

雷达：横轴 model、指标 accuracy/latency_ms/energy_j、均值聚合；逐项指定方向为越大/越小/越小，并明确归一化。不能据此推导真实模型优劣。
