# Changelog

本文件记录 RPA 自动化助手的版本变更历史。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [1.0.0] - 2026-08-05

首个正式发布版本。

### 新增

- **内置浏览器**：基于 Chromium (QWebEngineView)，支持地址栏导航、前进/后退/刷新/主页
- **操作录制**：类 Playwright Codegen，实时捕获点击/输入/选择/按键，支持删除单步和调整顺序
- **代码生成**：录制动作一键导出 Python / JavaScript / TypeScript / C# / Java 脚本，带语法高亮
- **右键复制定位器**：内置浏览器右键菜单，支持复制 CSS / XPath / ID / Role / 文本定位器
- **定时任务调度**：支持间隔/Cron/一次性触发，全局并发信号量(3) + 任务级锁 + max_instances=1
- **执行历史**：记录最近 20 次运行（时间/状态/耗时/错误），界面可查看
- **系统托盘通知**：任务成功/失败后弹出桌面通知
- **网页数据抓取**：按规则抓取 + 整表抓取 + 多页翻页抓取
- **Excel 导出**：openpyxl，列宽自适应
- **Oracle 数据库**：瘦模式连接，SQL 查询（fetchmany 分页限制 5000 行），工作线程异步执行
- **浏览器设置**：UA / 代理 / 视口 / 超时 / 无痕模式 / SSL 忽略 / 图片开关
- **颜色自定义**：语法高亮、日志级别、高亮色共 19 项可配置
- **日志系统**：文件轮转（5MB x 7）+ 界面面板 + 控制台
- **打包脚本**：PyInstaller 目录模式（推荐）和单文件模式

### 工程化

- TaskStore 线程安全（threading.Lock + 原子写入 tempfile + os.replace）
- 页面加载策略优化（domcontentloaded 优先，networkidle 超时降级）
- Oracle/Settings 操作移至 QThread，避免阻塞 UI
- 独立 Chromium 缓存目录，启动时清理损坏的索引文件
- .gitignore / LICENSE / README.md / TEST_PLAN.md / CHANGELOG.md
