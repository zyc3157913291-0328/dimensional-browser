# dimensional-browser 开发日志

> **这份文档是什么**：本项目的**开发日志与经验沉淀**。项目状态、已交付内容、
> 未决项与验收要点记在这里，不再写进跨机交接文档。
> 跨机交接目录（`$env:DSH_HANDOFF`）只保留**与具体项目无关**的环境与工具经验。
>
> **详细验收指引在 [`HANDOFF.md`](HANDOFF.md) 里**（297 行：七条验收点逐条「看哪里 / 预期」、
> 两个真 bug 的根因、五个未决项的完整分析、本轮文件清单、验证命令、回滚手段）。
> 本文档记录**状态与关键信息**，不搬运全文；要看操作细节请打开 `HANDOFF.md`。
>
> 路径写法：本文档用 `$env:DSH_PROJECTS\dimensional-browser` 指代本项目目录
> （本机即 `I:\AI\DSH\workspace\projects\dimensional-browser`）。
> **HANDOFF.md 与 README.md 里写的是迁移前的历史路径**，属事实记录，照原样保留。
> 下面「运行方式」里的 `I:\Anaconda\python.exe` 也是**本机现有事实**
> （本机 Anaconda 就装在 `I:\Anaconda`，与 `$env:DSH_ANACONDA` 的定义一致），不是历史残留。

---

## 项目速览

| 项 | 值 |
|---|---|
| 名称 | 量纲关系浏览器（dimensional relation browser） |
| 位置 | `$env:DSH_PROJECTS\dimensional-browser`（历史路径 `I:\AI\DSH\workspace\dimensional-browser`） |
| 运行方式 | `cd $env:DSH_PROJECTS\dimensional-browser; $env:DSH_ANACONDA\python.exe server.py` → 自动开 http://127.0.0.1:8765/<br>（`DSH_*` 为空时用回退值：`cd I:\AI\DSH\workspace\projects\dimensional-browser` + `I:\Anaconda\python.exe`。变量对已启动的 DSH 不可见，重启后正常） |
| 常用参数 | `--port 9000` 换端口 / `--no-browser` 不自动开浏览器 / `--verbose` 打印每个请求 |
| 依赖 | **零第三方依赖**，只用 Python 标准库。启动前跑数据自检，不通过就拒绝启动。 |
| 当前状态 | **第四轮七条问题全部改完并自验通过，等用户验收**（自检绿 · 501 断言 · 5 个浏览器验证脚本全绿） |
| 端口 | 8765 已释放，后台任务已清理 |
| 用户计划 | 晚上做最终验收并调整 |
| 版本控制 | **工作区不是 git 仓库，没有一键回滚。** 建议用户验收满意后先 `git init` 打基线。 |
| 项目内文档 | `HANDOFF.md`（第四轮交付备忘）、`README.md`（设计与使用说明，607 行）、`docs/2026-05-26-page-schema.md`（页面槽位规范）、`docs/2026-05-26-dimensional-relation-browser-design.md`（设计文档 40 KB） |

**数据规模**（第四轮交付时）：单位 27 · 物理量 57 · 公式 67 · 代数规则 13 · 形状模式 6 ·
领域 9 · 符号表 60 条 · 索引量纲 37 种 · 生成边（求导 22 / 积分 27，其中反读 24）。

---

## 一、第四轮交付内容（2026-09-23）

### 1.1 用户要验收的七条

| # | 问题 | 修法要点 |
|---|---|---|
| ① | 算法枚举重复（`N·m` / `m·N`） | 查「能量」→「展开更多（算法枚举同量纲组合）」，条数从 **18 降到 14**，不再出现 `m·N` / `W·s` / `m³·Pa`。实现：`parser.canonical_expr()`（新增）——除法与负幂统一成指数、因子按基名排序、同名基指数相加；它只是**比较键**，界面显示仍走单位渲染器 |
| ② | 求导/积分成对 | 查「能量」→「由物理式子生成」：1 条求导 + **2 条积分**，两条积分都带紫色「反读」徽标并写出原方向。**根因不是数据缺失，是建模漏了一维**：数据里 derivative 9 条、integral 只有 3 条看着不对称；但求导与积分互为逆运算，每条 derivative 都同时断言了一条 integral。现在建树时双向读，数据里已显式声明的（冲量定理）自动优先 |
| ③ | 定义式三态 + 补录 | 「力」→ 列出 `F = m·a`；「长度」→「**SI 基本量，没有定义式**」+ 说明 + 标「本条目不适用」；「能量」→「总称量纲，没有唯一定义式」+ 说明；「Wb」→ 这个量纲没登记物理量 →「**暂缺**」+ 补录按钮。**复查结论：原先 38 条缺定义式，31 条是数据缺失已补**（现 49/57 有定义式）；余下 8 条本来就不该有（6 个 SI 基本量 + 能量、化学势能两个总称量纲），已用 `defined_as` 字段标明理由。**当前「暂缺」为 0 条**——所以「暂缺 + 补录」这条通路要用未登记量纲的页面（如 `Wb`）来验 |
| ④ | 输入即匹配 | `dcqd` → 电场强度（完全相同·拼音首字母）；`dianchang` → 电场强度（名称开头·拼音全拼）；`电场` → 电场强度（名称开头）；`kgm` → 千克、质量…（**输入更长，包含该名称**）；`ohmxyz` → 欧、电阻；`电场强渡` → 电场强度（字符部分重合，可能是错字）。键盘 `↑` `↓` 移动、`回车` 采用高亮项、`Esc` 关闭、`Ctrl+K` 聚焦。拼音用 `pypinyin` 但**只在构建期**——生成 629 字的表存进 `data/pinyin.json`，运行时仍零依赖（全量扫描约 1 ms）。**没做语义理解**：候选只有 84 个，词向量/LLM 与「零依赖、可离线打包」冲突，这层留给条目数据的 `aliases` 字段覆盖 |
| ⑤ | 页面槽位统一 | 每个词条卡片都是**相同的 6 个槽位、相同顺序**：`常见写法 → 同量纲异构 → 定义式 → 等价写法 → 由物理式子生成 → 相关物理式子`。「常见写法」现在**逐行**：写法 + **中文读法**（引擎沿 AST 生成，如 `kg·m/s^2` →「千克·米每二次方秒」）+ 可选补充说明。空槽位分两类、视觉可辨：灰底斜体 = **不适用**（附为什么），虚线框 + 按钮 = **暂缺**。规范文档 `docs/2026-05-26-page-schema.md`（新建），覆盖审计 `audit_pages.py` |
| ⑥ | 历史记录移入搜索框 | 页面下方常驻历史横条**已删掉**。聚焦空搜索框 → 面板弹出历史（时间倒序、每行带时间、行尾 `×` 删单条、面板标题右侧「清空全部」）。输入文字 → 面板切换为候选，标题右侧留了「**看历史（N 条）**」的切换入口（否则已有文字时历史就没有入口可达） |
| ⑦ | 前进 / 后退 | 搜几个词条 → 浏览器后退应逐条退回，前进同理，**退到头回到首页分类浏览**（输入框与地址栏都清空）。原因：原来用 `replaceState` 写 hash——那是**故意**避开历史记录的写法，所以后退键永远退不回去。现在每次导航 `pushState`，`popstate` 复原 |

### 1.2 顺带修的三个真 bug / 改造

1. **数据错误，原本的自检抓不到**：`magnetic_force_lorentz` 的 tex 是 `F = q·v·B`，
   但 `signature.lhs.symbol` 写成了 `B`。齐次性检查**完全放行**（量纲确实对得上），
   后果是符号表里多出一行「B = 磁场力」且没有跳转链接——**看界面完全正常**。
   加了一条自检规则「**同一签名里一个符号只能有一个量纲**」，它当场抓出了这个；数据修完后自检转绿。
2. **分母的幂次没生效**：`_derive_rhs_dim` 的 `div` 分支只累加 `dim` 不看 `exp`，
   `E_v = I_v/r²`（照度＝发光强度除以距离平方）会少减一阶长度。
   此前数据里没有带幂次的分母，所以一直没暴露。
3. **打包路径分离**（按用户说的分发目标）：新增 `engine/paths.py`，把「随程序分发的只读资源」
   与「用户可写数据」分开。打包后资源取 `sys._MEIPASS`，而 `submissions.json` 落在**当前工作目录**——
   否则 exe 一退出，临时目录连同提交一起被删。**但 PyInstaller 还没实打过**，只是把结构准备好了。

### 1.3 五个未决项（等用户定方向，详见 `HANDOFF.md` §3）

- **A** `▦ 矩阵运算` 边类型不可达。
  全库 55 条关系边只有 `equivalence` 43 / `derivative` 9 / `integral` 3，
  **全库没有任何 `kind: "matrix"` 的关系边**，于是这些都白写了：
  `EDGE_KINDS["matrix"]` 与界面上的 ▦ 标记、图例行；`algebra.det` / `trace` / `matmul`
  （有实现有测试，但数据里没有公式用它）；两个 `structure: matrix` 的条目（转动惯量、应力）。
  **建议**：给 `moment_of_inertia_integral` 补一条 `kind: "matrix"` 的关系边
  （`I = ∫∫∫ r²ρ dV` 正是「矩阵元素有量纲」的典型例），或加一条行列式/迹的示例公式。
  改动很小，但需要用户点头——它触及「边必须来自现实式子」这条原则。
- **B** 转动惯量的积分关系量纲不自洽，被变量校验静默丢弃。
  `moment_of_inertia_integral` 的关系边写的是
  `{"kind": "integral", "from": "kg/m^3", "to": "kg·m^2", "wrt": "x", "order": 3}`。两个问题：
  ① `wrt: "x"` 量纲是 L，但公式签名里的积分变量是 `V_vol`（体积元，L³）——变量量纲校验不通过，
  这条边**被静默过滤掉了**，所以「转动惯量」页面的「由物理式子生成」现在是空的（显示为不适用）；
  ② 即便把变量对上，算式也不闭合：`dim(kg/m³) + L³ = M ≠ dim(kg·m²)`——`from` 漏了 `r²` 这个因子。
  **建议**：改成 `from: "kg/m"`（`r²ρ` 的量纲 M·L⁻¹）、`wrt: "V"`、`order: 1`。
  **和 A 一起改是最省事的。**
- **C** 公式列表在每张卡片下重复。「能量」量纲下有 10 张卡片，每张都列出同一批相关公式——
  实测这一页渲染了 **180 行公式**。教学上有强化作用，但确实冗余。
  **可选方向**：只有第一张卡片列全，其余折叠成一行「与本页首卡相同（N 条）」；
  或整页合并成一个「本页涉及的公式」区块放在底部。
- **D** 摄氏 / 华氏温度偏移未实现。单位表只有换算因子，没有 `offset` 字段，所以只支持线性单位。
  热力学温度本身已收录（量纲 Θ），但 `t = T − 273.15` 这个零点差**在量纲上看不出来**——
  这是量纲法的边界，不是实现缺陷。要不要支持由用户定。
- **E** 其他已知限制（都在 `README.md`「已知限制」一节）：
  - 右侧目录宽度 ≤ 1400px 时隐藏（避免挤占正文），窄屏形态未设计
  - PyInstaller 未实打
  - 拼音表需手工重建（新增中文条目后跑 `gen_pinyin_table.py`，`selfcheck.py` 会提示）
  - 联想是名称检索，**不是语义检索**

### 1.4 本轮文件清单（详见 `HANDOFF.md` §4）

**新增**

| 文件 | 作用 |
|---|---|
| `engine/matcher.py` | 即时联想的匹配打分（8 级梯度，全量扫描 ~1 ms） |
| `engine/paths.py` | 只读资源 / 用户可写数据 的路径分离（为打包准备） |
| `data/pinyin.json` | 构建期生成的 629 字拼音表（全拼 + 首字母） |
| `gen_pinyin_table.py` | 生成上面那张表（**唯一用到 pypinyin 的地方，构建期**） |
| `audit_pages.py` | 页面槽位覆盖审计（内容体检，非启动阻塞） |
| `check_js.py` | 抽出 `<script>` 做 `node --check` + 顶层重复声明检查 |
| `shoot.js` / `shoot_ac.js` | 定尺寸截图工具 |
| `verify_round4.js` | 本轮七条的浏览器验证 |
| `migrate_round4.py` | 本轮数据迁移脚本（**幂等**，可重复跑） |
| `docs/2026-05-26-page-schema.md` | **页面槽位规范**（先写规范再改页面） |

**修改**

| 文件 | 改了什么 |
|---|---|
| `engine/parser.py` | 新增 `canonical()` / `canonical_expr()` 结构规范形 |
| `engine/tree.py` | 反读对偶边、去重键换规范形、补录按 kind 分流、常见写法逐行展开、槽位字段 |
| `engine/registry.py` | `defined_as` 字段、分母幂次修正、新增符号歧义自检规则 |
| `engine/submissions.py` | 四种补录 `kind` + `target_qid` |
| `engine/api.py` | `/api/suggest` 端点、新字段序列化、联想缓存加锁 |
| `web/index.html` | 统一页面模板、联想面板、历史移入搜索框、前进后退、四种补录表单 |
| `data/quantities.json` | +2 个 SI 基本量、37 条 note、6+2 条 defined_as |
| `data/formulas.json` | +32 条定义式公式、修正洛伦兹力 lhs 符号 |
| `data/symbol_glossary.json` | +4 条符号（宽度 / 周期 / 应变 / 立体角 / 摩尔气体常量 / 热力学温度） |
| `README.md` | 重写相关章节，补上本轮的设计取舍 |

---

## 二、验证记录（最近一次全绿）

```powershell
cd $env:DSH_PROJECTS\dimensional-browser   # 变量为空时用 I:\AI\DSH\workspace\projects\dimensional-browser

$env:DSH_ANACONDA\python.exe selfcheck.py     # 自检全部通过
$env:DSH_ANACONDA\python.exe test_engine.py   # 通过 501 项断言，67 个测试函数 / 全部通过
$env:DSH_ANACONDA\python.exe check_js.py      # exit=0，无重复声明
$env:DSH_ANACONDA\python.exe audit_pages.py   # 槽位覆盖审计

# 先起 server，再跑（每个都会写截图到 ui-shots/）
node verify_ui.js        http://127.0.0.1:8765   # 界面渲染
node verify_features.js  http://127.0.0.1:8765   # 历史 / 符号表 / 补录
node verify_fixes.js     http://127.0.0.1:8765   # 定义式 / 候选 / 目录 / 旧 bug 回归
node verify_round3.js    http://127.0.0.1:8765   # 首页分类 / 目录样式 / 折叠
node verify_round4.js    http://127.0.0.1:8765   # 本轮七条
```

> 迁移记录里另有一笔（见 `工作状态.md` 10.3 第 10 项）：迁移到另一台机器后
> 自检全绿 —— `selfcheck.py` 通过 · `test_engine.py` **501 断言 / 67 函数全过** ·
> `check_js.py` exit=0 · `audit_pages.py` 覆盖报告与第四轮一致。
> **但浏览器验证脚本还没在那台机器上跑过**（需先起 `server.py`，端口 8765）。

**本轮新增的断言**（都是锁住这次踩过的坑）：

- 规范形：`N·m` 与 `m·N` 必须同键，枚举结果里不得出现已有写法的等价形式
- 成对：每条 derivative 关系的**源量纲**页面上必须有积分边
- 槽位：无定义式的条目必须由 `defined_as` 解释清楚；SI 七个基本量的量纲都有条目
- 常见写法：每条都必须有中文读法
- 推导：分母的幂次必须生效（`E_v = I_v/r²`）
- 符号：同一签名里一个符号只能有一个量纲（并给反例断言自检会拦下）
- 补录：四种 kind 各填各的槽位；pending 一律不填槽；说明类不覆盖已有描述
- 联想：`abc` 要能命中 `ab`，但单字符候选不做反向命中（防噪声）

⚠️ `verify_features.js` 会真的提交一条补录来验证写入链路——跑完请删掉
`submissions.json`（那是测试数据）。**交付时已经删干净了**，项目目录里没有它。

---

## 三、回滚与基线

**没有 git 仓库**（工作区不是 git repo），所以没有一键回滚。可用的手段：

- `migrate_round4.py` 是**幂等**的，重复跑不会叠加改动；它记录了本轮所有数据变更，
  要撤销就照它反向改。
- 数据改动集中在 `data/` 三个文件（`quantities` / `formulas` / `symbol_glossary`），
  都是可读 JSON，手工改回去不难。
- 代码改动是**增量**的（新增模块 + 新函数），没有删除既有功能。

**建议**：用户验收满意后第一件事是 `git init` 打一个基线。

---

## 四、环境注意（本项目踩过的）

1. 会话里跑 Chrome headless 需要放宽沙箱（OpenProcess / 命名管道会被拦），
   `danger-full-access` 够用。
2. 控制台默认 GBK，脚本开头都强制 `sys.stdout.reconfigure(encoding="utf-8")`；
   用 `-c` 传中文脚本会被 PowerShell 吃掉引号，所以复杂逻辑都落成 `.py` 文件跑。
3. 读无 BOM 的 UTF-8 文件要显式 `-Encoding UTF8`。

---

## 五、历史沿革（来自跨机交接文档〇节）

- 项目位置在历史交接文档里记作 `I:\AI\DSH\workspace\dimensional-browser`；
  **现在在 `$env:DSH_PROJECTS\dimensional-browser`**。
- 第四轮的状态记录：**七条问题全部改完并自验通过**（自检绿 · 501 断言 · 5 个浏览器验证脚本全绿），
  运行方式 `cd dimensional-browser; I:\Anaconda\python.exe server.py`
  （当时的写法；现在项目在 `projects\` 下，应写 `cd $env:DSH_PROJECTS\dimensional-browser`），
  **端口 8765 已释放、后台任务已清理**，用户计划晚上做最终验收并调整。
- 交接文档提醒：工作区**不是 git 仓库**，没有一键回滚，建议验收满意后先 `git init` 打基线。
