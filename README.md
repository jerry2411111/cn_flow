# A股资金流向（cn_flow）

沪深 A 股的资金流向排行，以及一个回归分析：资金流入之后，价格是涨还是跌。数据来自通达信公开行情服务器的逐笔成交，每个交易日收盘后自动更新。只做分析展示，不下单，也不给买卖建议。

![界面](docs/screenshot.png)

## 下载使用（Windows）

1. 打开 [Releases](../../releases/latest)，下载 `cn_flow-windows.zip`。
2. 解压到任意文件夹，双击 `cn_flow.exe`。

不用安装 Python，也不用下载历史数据。第一次打开时，程序会从本仓库下载最新的计算结果（约 3 MB），之后每 20 分钟检查一次有没有新结果。

- 需要 Windows 10 或 11 自带的 Edge WebView2。极少数没有它的电脑，可以从 [微软官网](https://developer.microsoft.com/microsoft-edge/webview2/) 安装。
- 结果文件保存在 exe 旁边的 `results` 文件夹，所以请解压到有写入权限的地方，例如桌面或“文档”，不要放进 Program Files。
- 第一次打开时 Windows 可能弹出 SmartScreen 提示（exe 没有付费签名）：点“更多信息”，再点“仍要运行”。

## 每天怎么更新

本仓库的 GitHub Actions（[daily.yml](.github/workflows/daily.yml)）在每个交易日北京时间 17:30 左右运行：

1. 从通达信服务器取当天全部沪深 A 股的逐笔成交；
2. 重算排行和回归；
3. 把结果发布到 Release [`results`](../../releases/tag/results)。

程序只下载这些结果。逐笔汇总的历史数据按月存放在 Release [`history`](../../releases/tag/history)（每月一个 tar 文件），供下一次运行使用，使用者不需要下载。[`status.json`](status.json) 记录最新一次更新到哪一天。

## 页面上有什么

- **个股排行、板块排行**：按过去 1、3、7、14、30、60、120 个交易日的净流入排序，可以选三种口径：
  - 占流通市值：净流入 ÷ 流通市值；
  - 占成交额：净流入 ÷ 同期成交额；
  - 净流入金额。
  
  板块包括通达信一级、二级行业和概念板块。每个交易日收盘后更新一次。
- **回归分析**：每天在全部股票之间做一次回归，看资金流入比当天平均高 1 个标准差的股票，第二天和之后一周的实际涨跌（和 0 比），并给出 Newey-West t 值。也可以只看流入期间价格上涨或下跌的股票。
- **说明**：每一列、每个数字的定义。

## 定义要点

- 净流入 = 主动买入的成交额 − 主动卖出的成交额。方向用服务器给每笔成交的买卖标记，不用买卖价差，因为历史逐笔里没有当时的报价。集合竞价、盘后定价成交不计入。
- 涨跌停：当天最高价是涨停价时，涨停价上的成交不计入（封板时的成交都被标成“主动卖”）；跌停同理。
- 涨跌按除权除息复权。范围是沪深 A 股（主板、创业板、科创板），北交所暂不包括。
- 回归是统计分析，不是交易回测：涨跌没有扣交易成本，也不设账户资金。

## 自己采集数据（可选）

想自己从头采集，或者改代码，需要 Python 3.13：

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-app.txt
backfill.cmd            :: 最近 245 个交易日的逐笔汇总，约 2.5 小时（6 个进程、96 个连接）
.venv\Scripts\python -X utf8 analyze.py
create_shortcut.cmd     :: 桌面快捷方式，打开 app.py
```

之后每天运行 `daily_update.cmd`（`register_daily_task.cmd` 可以把它设成 Windows 计划任务）。本地结果比 GitHub 上的新时，程序不会用下载的结果覆盖它。

| 文件 | 作用 |
|---|---|
| `tdx.py` | 连接通达信服务器、取数（可用的接口和买卖标记的含义见文件开头） |
| `collect.py` | 下载逐笔，按股票、按天汇总到 `data/days/YYYYMMDD.parquet`；参考数据在 `data/ref/` |
| `analyze.py` | 窗口合计、排行、回归，结果写到 `results/` |
| `app.py` | 桌面程序窗口（pywebview + Edge WebView2） |
| `ui.py`、`ui/index.html` | 页面和只读数据服务；`python ui.py` 也可以在浏览器里打开 http://127.0.0.1:8790 |
| `sync.py` | 从 Release `results` 下载最新结果 |

---

*English:* A-share (Shanghai/Shenzhen) money-flow rankings over 1–120 trading days, plus a cross-sectional regression of next-day and next-week returns on past inflow. The data comes from TDX tick history, and a GitHub Actions workflow refreshes it after every trading day. The Windows program downloads only the published results, so users never fetch the history. It is analysis only: it gives no trading advice and places no orders.
