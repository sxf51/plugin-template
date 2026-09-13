# 插件模板 · plugin-template

> English: [README.en.md](README.en.md)

零第三方依赖、可直接运行的插件，把宿主开放的每一个扩展点各用一次。功能本身是"记笔记 + 传附件"。

默认 `enabled: false`。在插件管理页点"启用"，或把 `plugin.yaml` 的 `enabled` 改为 `true`。
Redis 不可用时自动退回 JSON 文件。本目录已加入 `.gitignore`。

## 复制一份开始

```bash
cp -r plugins/plugin-template plugins/my-plugin
```

```bash
cd plugins/my-plugin
uv run python main.py rename my-plugin      # 清单、工具、子智能体、命令一次改完
uv run python main.py doctor                # 确认没有漏掉的地方
```

`rename` 只改这个插件**实际注册过**的名字，所以像 `template_list` 这种框架词汇不会被误伤。
改完之后：

1. 改 `plugin.yaml` 的 `version`、`description`、`metadata`。
2. 把 `tests/test_plugin_*.py` 换成你自己插件的行为测试；`tests/test_plugin_contract.py` 不用动。
3. 删掉用不上的文件。四个固定模块名都是可选的。
4. `enabled` 改为 `true`，在管理页点重新加载。

## 文件

| 文件 | 内容 |
| --- | --- |
| `plugin.yaml` | 清单：元数据、依赖、日志级别、`ui.pages`、`ui.panels`（9 种小部件）、`config.commands`、`config.tool_access` |
| `requirements.txt` | 依赖声明格式与被拒绝的写法 |
| `_conf_schema.json` | 10 种字段类型、`label`/`hint`/`options`/`secret`/`invisible`、`options_source` |
| `SKILL.md` | 技能 markdown，宿主自动发现，无需 `register_skills` |
| `hooks.py` | 10 个生命周期拦截点各一个 handler |
| `tools.py` | 三种工具注册方式、依赖注入、`consequential`、输入 Schema、存储服务 |
| `subagents.py` | 装饰器与显式两种子智能体注册、受限工具视图、`vote` 节点投票 |
| `web.py` | 两种端点注册方式、GET/POST/PUT/DELETE、附件上传/列出/下载/删除、二进制、SSE、调用工具执行器 |
| `pages/console/` | 沙箱页面，用满宿主桥接的每个方法，中英双语 + 明暗主题 |
| `store.py` | 非固定模块名的模块如何加载；Redis 与文件两套后端；上传文件的文件名与扩展名校验 |
| `pyproject.toml` | 独立开发用的 ruff / pytest 配置与 dev 依赖；`package = false`，没有要构建的东西 |
| `main.py` | 不开宿主也能跑插件：查看能力、体检、调工具、发钩子、打端点、开页面、改名 |
| `tests/test_plugin_contract.py` | **通用**：从插件自己的声明推导，复制后一行不改 |
| `tests/test_plugin_template.py` | 本插件自己的行为，换成你自己的 |
| `tests/conftest.py` | fixture 与后端选择 |
| `tests/harness/` | 假宿主与开发工具，**复制后一行不改**（见下表） |
| `.github/workflows/` | CI（每次推送跑 lint + 测试）与发版（版本号变了就自动打 tag 并发 Release），**复制后一行不改** |
| `.github/scripts/plugin_version.py` | 读取并校验 `plugin.yaml` 里的版本号，供发版流程使用；`pyproject.toml` 若写了静态版本号会直接报错 |

`tests/harness/` 里的东西都不认识本插件，复制后不用改：

| 文件 | 内容 |
| --- | --- |
| `host.py` | `RealHost` / `FakeHost` / `select_host()`，统一接口 |
| `shim.py` | 把 `extension.plugin` / `.hook` / `.plugin_web` 注入 `sys.modules` |
| `loader.py` | 清单、配置 Schema、命令、requirements 的规则（独立那一侧的实现） |
| `fakes.py` | 假的工具表、钩子管理器、子智能体注册表、存储、Web 上下文、工具执行器 |
| `checks.py` | 通用检查；测试与 `doctor` 共用同一份 |
| `pageaudit.py` | 页面静态检查 + 提取页面 JS 调用的端点 |
| `report.py` | 能力清单 |
| `devserver.py` | `main.py serve` 的实现与开发版桥接脚本 |
| `rename.py` | `main.py rename` 的实现 |

插件根目录和 `tests/` 下都**没有** `__init__.py`。加了它，pytest 会把该目录排在仓库根目录之前塞进
`sys.path`，`tools.py` 会盖掉项目的 `tools` 包，测试收集失败。宿主按文件路径导入插件模块，不需要包标记。
`tests/harness/` 是例外：它要被 `import harness` 导入，所以那一层有 `__init__.py`。

## 选扩展点

| 目的 | 用法 |
| --- | --- |
| 加一个模型可调用的能力 | `tools.py` 里注册工具 |
| 加一条斜杠命令 | `plugin.yaml` 的 `config.commands` |
| 在消息进入 Agent 前处理掉 | `hooks.py` 的 `before_route`，设 `route_outcome` |
| 影响路由但不硬编码 | `router_hints`，或命令上的 `planner_hints` |
| 拒绝某次工具调用 | `before_tool_call` 里设 `context["denied"]` 与 `context["reason"]` |
| 统一改写工具结果 | `after_tool_call` |
| 加一个 planner 可选中的专家 | `subagents.py` 里注册子智能体 |
| 参与 `vote` 节点 | 子智能体 `run` 里识别 `task["_vote_spec"]` |
| 加指标卡片或图表 | `plugin.yaml` 的 `ui.panels` + 自己的端点，不写前端代码 |
| 做完整交互页面 | `pages/<name>/` + `ui.pages` |
| 存数据 | `runtime_context["storage"]` |
| 存用户上传的文件 | `storage.dir(...)` 存字节 + 一条索引记录 |
| 让用户改配置 | `_conf_schema.json` |
| 让用户从已配置的模型里选 | 字段上写 `options_source: llm.providers` / `llm.models` |

## 命令

`config.commands` 里的命令在以下任一情况会被静默丢弃，不报错也不出现在 `/help`：

- 命令名不以 `/` 开头
- 与内置命令重名
- `route` 不是 `task` 或 `tool`
- `priority` 不是整数
- `route: tool` 但 `tool` 指向的工具未注册
- `requires_tool` 指向的工具未注册

`priority` 分档：5–20 关键，21–50 常规，51+ 辅助。

## 配置

`plugin.config = {**plugin.yaml 的 config, **_conf_schema.json 保存的值}`。同一个 key 不要两边都写，
schema 的值会覆盖清单里的默认值。用户可改的放 schema，`commands` / `tool_access` 放清单。

字段类型：`string`、`text`、`int`、`float`、`bool`、`object`、`list`、`dict`、`template_list`、`file`。
`file` 与 `template_list` 的值是列表，`object` 与 `dict` 是对象。字段可用 `label`、`description`、
`hint`、`default`、`options`、`option_labels`、`invisible`、`secret`；`object` 用 `items` 递归声明子字段。

候选值来自运行态时用 `options_source` 而不是 `options`：

| 取值 | 效果 |
| --- | --- |
| `llm.providers` | 列出本部署已配置的全部 provider，含已禁用的 |
| `llm.models` | 列出 `depends_on` 指定的兄弟字段所选 provider 的模型；该字段为空时用默认 provider 的模型 |

下拉框可清空、也可手输，空值表示"用宿主默认"。LLM 接口不可达时退化为文本框。
已保存的值不会再按实时列表校验。

`secret: true` 只在表单里打码，落盘仍是明文，密钥请走环境变量。
`invisible: true` 的字段不出现在表单里，但仍进入 `plugin.config`。

## 存储

`runtime_context["storage"]` 是一份已绑定本插件作用域的对象，调用时不传插件名：

| 方法 | 效果 |
| --- | --- |
| `storage.dir(*parts)` | 本插件私有目录，按需创建 |
| `storage.path(*parts)` | 本插件私有文件路径，父目录按需创建 |
| `storage.resolve(path)` | 该路径属于本插件则返回它，越界或不存在返回 `None` |
| `storage.user_file(path)` | 解析宿主替用户存下的文件（如聊天附件），越界返回 `None` |
| `storage.key(*parts)` | 本插件私有的 Redis key |
| `storage.client()` | 共享 Redis 客户端，已配重试与健康检查；不可用时返回 `None` |
| `storage.available()` | 后端此刻是否可用 |

路径分段会被安全化，`..` 出不了本插件目录。收到调用方给的路径时先过 `resolve` 或 `user_file`，
不要直接打开。

## 上传与删除

- 落盘保留用户上传时的文件名，但客户端给的字符串不会被原样使用：`store.py` 的 `safe_stem()`
  去掉目录、把白名单外的字符换成 `_`、剥掉首尾的点和空格、限长，并避开 Windows 设备名。
- 扩展名单独判定，要过 `ALLOWED_EXTENSIONS` 白名单；不在白名单时按声明的 MIME 兜底，
  再兜底到 `.bin`。所以 `payroll.pdf.exe` 落盘是 `payroll.pdf.pdf`，不会是可执行文件。
- 重名时自动让路：`report (1).pdf`、`report (2).pdf`。占名用的是独占创建而不是 `exists()`
  判断，两个上传同时抢一个名字不会都被告知"没人用"；`Report.pdf` 与 `report.pdf` 算不算同名，
  交给文件系统回答，不在代码里再写一条规则。
- 下载回的 Content-Type 由扩展名决定，不回显客户端声明的类型。
- 删除要同时删索引记录和磁盘字节，见 `AttachmentStore.remove()` 与 `discard_files()`；
  `actions/prune` 走同一条路。
- 上传大小在页面和后端各限一次。页面那次只是提示，后端那次才生效。

## 页面

- 不要自己写 `__bridge.js` 的 script 标签，宿主会注入。
- 注释里不要出现字面的 head 开标签。宿主在源文本里找第一个 head 开标签插入桥接脚本，注释里的也算；
  踩中后页面能加载但一行脚本都不执行，表现为白屏。
- 不能引任何外站资源，CSP 会静默拦掉，静态审查会记一条 finding。
- 页面只能通过 `window.CapstonePluginPage` 访问后端。
- `bridge.upload()` 在页面内读完字节再过桥。把 `File` 对象直接交给宿主会因跨渲染进程缺少文件读权限
  而失败，报 `net::ERR_ACCESS_DENIED`。
- 用数据渲染出来的文案没有 `data-i18n`，语言切换时要自己重绘，见 `app.js` 的 `paint()`。

## 其它注意事项

- `enabled: false` 时工具、钩子、子智能体、Web 端点、`SKILL.md` 一个都不注册，管理页状态为 `disabled`。
- 插件目录里只放源码。运行时数据由宿主放在 `data/` 下，根目录 `data/` 已被 Git 忽略。
- 工具要声明 `description` 与 `parameters`（JSON Schema 对象），否则 planner 只能靠猜。
- 有真实副作用的工具要声明 `consequential = True`，人工审批闸门只看这个属性。
- `before_tool_call` 拒绝调用时要同时设 `denied` 和 `reason`，且返回的 context 必须保留 `payload`；
  `after_tool_call` 必须保留 `result` 键。
- 钩子 handler 返回新 dict，不要原地改传进来的那个。

## main.py：不开宿主跑插件

```bash
uv run python main.py inspect                     # 本插件注册了什么
uv run python main.py doctor                      # 通用检查，问题 + 怎么改
uv run python main.py call <tool> '<json>'        # 走工具执行器，前后钩子照常触发
uv run python main.py hook <point> '<json>'       # 手动触发一个拦截点
uv run python main.py web GET stats               # 打一个端点
uv run python main.py command '/my-plugin-thing'  # 走命令路由
uv run python main.py serve                       # 在浏览器里打开页面
uv run python main.py rename <new-name>           # 批量改名
```

`serve` 用标准库起一个本地服务，托管 `pages/<name>/`，并注入一份**开发版桥接脚本**——方法名与宿主
注入的那份完全一致，底层改用 `fetch` 打到本进程，再转给插件真正的处理器。所以页面代码一行不用改，
点按钮真的调到插件的 Python，SSE、上传、下载、二进制预览都是真的。

浏览器控制台里 `window.__setContext({locale:'zh', isDark:true})` 可以切语言和主题，验证页面有没有跟着重绘。

`serve` **不复刻**沙箱、CSP 与鉴权。页面在这里能跑，到了控制台仍可能被 CSP 拦掉——那部分由
`doctor` 的页面检查负责。

## 通用自检

`tests/test_plugin_contract.py` 与 `main.py doctor` 跑的是同一份检查（`tests/harness/checks.py`），
全部从插件自己的声明推导，改功能不用改检查：

| 检查 | 抓到的是 |
| --- | --- |
| 每条命令都能被运行时收下 | 命令被静默丢弃，`/help` 里查无此命令 |
| `tool_access` 的键都是已注册工具 | 白名单写错名字，形同虚设 |
| 每个工具有 description、输入 Schema、`consequential` | planner 只能靠猜；审批闸门失效 |
| 工具名带得上插件名里的词 | 与内置工具重名是启动期致命错误 |
| 子智能体有 domain/capabilities，`allowed_tools` 都存在 | planner 永远路由不到它 |
| 钩子挂在合法拦截点、且用本插件名注册 | 卸载时回收不干净 |
| 声明的页面目录里真有 `index.html` | 页面被静默忽略 |
| 小部件的端点已注册、方法对得上、`source.field` 真能取到 | 面板空白或磁贴显示 "—" |
| **页面 JS 调的每个端点都已注册** | 只在浏览器里、上线后才暴露 |
| 页面没引外站资源、没自己写桥接脚本、注释里没有 head 标签 | 白屏 |
| 配置 Schema 合法，且没有和清单 `config:` 重复的键 | 表单渲染不出来；改了清单却不生效 |
| `requirements.txt` 没有被拒绝的写法 | 安装期报错 |
| `SKILL.md` 有 name/description | 技能列表里没有描述 |
| 每个 GET 端点被调用时不抛异常 | 500 而不是 4xx |

跑测试时末尾会打印一张能力清单：工具、子智能体、钩子、技能、命令、端点、页面、配置，
`main.py inspect` 打印同一张。最有用的信息往往是**清单里少了什么**。

## 独立开发

把插件目录复制到任何地方，不需要宿主源码就能跑测试：

```bash
uv sync
uv run pytest
uv run ruff check .
```

依赖只有 pytest、PyYAML、ruff。`tests/conftest.py` 先向上找 `src/extension/plugin.py`，
找到后**真的 import 一次**再决定用哪个后端——源码在不等于能导入，宿主的依赖也得装在当前环境里：

| 怎么跑 | 用哪个后端 |
| --- | --- |
| 仓库根 `uv run pytest plugins/plugin-template/tests` | 真的 `PluginManager`、真的注册表、真的工具执行器 |
| 插件目录内 `uv run pytest` | 假宿主：源码在上面，但插件自己的环境没装宿主依赖 |
| 复制到仓库外 | 假宿主：根本没有源码 |

每次运行的头一行会写明这次用的是哪个后端，不会出现"以为跑的是真宿主、其实退化成假的"。

测试正文两边完全相同，所以假宿主一旦和真宿主对不上，真宿主那一侧会先失败。

假宿主把 `extension.plugin`、`extension.hook`、`extension.plugin_web` 注入 `sys.modules`，
所以**插件源码一行都不用改**，装饰器照常生效。它还提供假的工具表、钩子管理器、子智能体注册表、
存储、Web 上下文与工具执行器。

独立那一侧跑不到的只有静态审查（需要宿主的 AST 扫描），它会 skip 而不是报错。
`pages/` 的渲染和依赖版本比对同样只在宿主里验证。

复制插件时 `tests/harness/` 要一起带走。

## 发版

宿主是按 **Git tag** 判断插件有没有更新的：一次发版就是一个名为 `v<plugin.yaml 里的 version>` 的 tag。
只往分支上推代码不算发版，用户那边不会提示更新——这是刻意的，免得半成品提交被当成新版本推给所有人。

所以流程只有一步：**改 `plugin.yaml` 的 `version`，合进默认分支**。版本号只写在这一处——`pyproject.toml` 用的是 `dynamic = ["version"]`，`uv.lock` 里也不记录，发版不需要碰它们，tag 由流程自动打

`.github/workflows/release.yml` 随后自动完成剩下的事：读出版本号 → 该 tag 已存在就什么都不做 →
不存在就先跑 lint 和测试 → 打 tag 并推上去 → 建 GitHub Release（`rc`/`a`/`b` 后缀会标成 prerelease）。
测试没过就不会有 tag，也就不会有任何用户看到这个版本。

版本号必须形如 `1.2.3`、`1.2.3rc1`、`1.2.3b2`，否则流程直接失败：

```bash
uv run python .github/scripts/plugin_version.py --check
```

用户侧对应的是插件详情页的「检查更新」——它只做一次 `git ls-remote --tags`，不会克隆仓库；
只有发布的版本号比已装的新，才会出现「可更新」。安装时填了版本标签的用户会被锁定在那个 tag 上，
不受新版本影响，直到他们显式换一个。

## 在宿主仓库内验证

```bash
uv run pytest plugins/plugin-template/tests -q
uv run ruff check plugins/plugin-template --no-respect-gitignore
uv run bandit -r plugins/plugin-template -c ../../pyproject.toml
```

本目录在 `.gitignore` 里，所以仓库根的 `ruff check .` 会跳过它，要显式加 `--no-respect-gitignore`。

手工：`enabled` 改为 `true` 并重启后端。管理页状态应为 `active`、审查 `pass`、配置表单渲染出全部 10 种
字段类型；侧边栏出现"模板控制台"；面板 9 种小部件都有数据；聊天里 `/template-ping` 由 `before_route`
直接回复，不进 Agent。

## 延伸阅读

- [插件开发指南](../../docs/zh/guide/plugin-development-guide.md)
- [插件前端指南](../../docs/zh/guide/plugin-frontend-guide.md)
- `image-generation-plugin`：用了同样这些扩展点的生产插件
