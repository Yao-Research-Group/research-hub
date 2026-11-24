<!-- GENERATED_BY: AI -->
# 本周证据页（2026-W47｜合格范例）

- **提交人**：张明（示例）｜**课题**：表格基础模型用于序列决策｜**覆盖时间**：2026-11-17 ~ 2026-11-23

## 1. 实验和结果
- **冻结基线对照（同历史长度 L512，`eval/run_grid_bench.py`）**：
  - 在 `CartPole-v1`（3 份历史 × 10 初态共 30 局）：`Model-M + RTG` 平均回报 `263.90`（`10/30` 达 500 步），高于同协议 `Baseline-T + RTG`（`138.60`，`2/30` 达 500 步）与 `Baseline-T + FQI10`（`169.07`，`0/30` 达 500 步），来源：`results/2026-11-21/cartpole_summary.json`。
  - 在 `Grid5`（每环境 128 局）：`Model-M + FQI10` 到达率 `100.0%`（平均回报 `-0.4341`，追平表格真解），`Baseline-T + FQI10` 为 `80.0%`，来源：`results/2026-11-21/grid5_summary.json`。
- **单步物理动态预测**：`Model-M` 零样本 1 步次态预测在 `CartPole-v1` 四维状态平均 $R^2 = 0.9728$（MAE `0.0075`），来源：`results/2026-11-21/dynamics_r2.csv`。

## 2. 代码变更
- `eval/adapters/model_m.py`（commit `a1b2c3d`）：修复 `use_cache=True` 在批推理时的状态偏移，新增全轨迹确定性重放校验脚本 `tests/test_replay_consistency.py`（21 份轨迹 `max_state_diff = 0.0`）。
- `train/distill_forward.py`（commit `e4f5a6b`）：新增禁止 `episode_id` 泄漏的单次前向排序蒸馏管线。

## 3. 文献
- 精读《In-Context Decision Pretraining via Tabular World Models》（2026），确认原实现仅做 1 步价值近似在稀疏延迟奖励下无法区分镜像动作；与我们 11-19 两轮 FQI 对比探针结论一致（notes/icdp_reading.md）。

## 4. 失败的尝试
- **CPU 预算 43 步无 FQI 蒸馏未形成真实控制**：在仅 43 步排序微调后，留出合成网格最优动作准确率由 `37.50%` 升至 `61.11%`，但实际 `CartPole-v1` 仅 `21.67` 步、`Grid5` 到达率 `0%`（`results/2026-11-21/distill_cpu_43steps.json`）。
- **KV-Cache 加速推理不可用**：开启 `use_cache=True` 时 logit 与无缓存前向存在 `0.91–1.73` 偏差，已强制回退为 `use_cache=False`。

## 5. 矛盾、不确定之处与待补证据
- 43 步失败属于算力步数不足未收敛，还是单次前向结构本身无法替代显式多轮 Bellman 迭代，目前无法区分，需待 GPU 跑满 1,000 步消融后确认。
- 训练侧新权重的历史前缀奖励覆盖率统计尚未交付，暂无法对新权重做同协议对齐评测。
