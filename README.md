# 湖表压实台（cc-compact-lab）

CC_Codex 二期数据工程产品线：Tornado + 原生 JS 时间轴 SPA + SQLite。

默认账号 `admin` / `123456`，端口 `7300`。

## 主链

1. 小文件清单：登记湖区碎文件与体积
2. 压实计划：把碎文件编入计划
3. 重写合并：执行合并任务并回写状态
4. 体积对账：计划前后体积对照报告
5. 台参数：现行压实阈值与阈值说明

## 阈值切流

阈值即口径，碎文件、计划、合并任务在入库 / 建单 / 启动时钉住当时阈值与
切流凭证号（切流芯片）。改阈值只走**切流账册**这一条道：

- 保存阈值先拿写锁（`BEGIN IMMEDIATE` + WAL），并发仓管排队提交，凭证链式追加不覆盖
- 阈值真变化才写 `threshold_cutovers` 凭证，在途计划与在册碎件同步切口径并盖凭证号
- 页面口径 / 在途单口径与现行阈值偏离时，挂入与启动一律拦下，并在
  `cutover_rejections` 留下凭证号（`GET /api/settings/cutovers` 可查）

## 预留切口（出题用，基线先通）

- 计划与清单体积同源
- 合并失败半态 / 重跑
- 对账快照与现行体积

## 本地

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
python run.py
```
