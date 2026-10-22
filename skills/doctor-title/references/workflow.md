# 联动协议与命令

## 安装和发现

三个仓库保持用户指定名称，但实际 Skill 根目录分别为：

```text
doctorTitle-skill/skills/doctor-title/SKILL.md
doctorArticle-skill/skills/doctor-article/SKILL.md
doctorImg-skill/skills/doctor-img/SKILL.md
```

宿主应安装或注册三个内层 Skill 目录。例如安装到同一个 `.kilo/skills/` 下，或将三个仓库的 `skills` 目录加入宿主搜索路径。不要把仓库根目录重命名成 Skill 名，也不要只安装 `SKILL.md` 而遗漏其引用和脚本。自定义 `metadata.requires` 仅供 Agent 理解，不是官方自动依赖管理功能。

脚本只做有限路径探测：显式 `--skills-root`、环境变量 `DOCTOR_SKILLS_ROOT`、脚本的安装位置/兄弟仓库、当前工作目录、`C:\demo`、用户的 Kilo/Claude/Codex 常见技能目录。不会扫描整块磁盘或自动改宿主配置。若宿主把三项分散安装，可重复指定 `--skills-root`。找到文件后 Agent 仍须实际读取。

## 命令

以下命令从 `C:\demo\doctorTitle-skill` 执行；`python` 必须是可运行的 Python 3.11+，不是 Windows 应用商店占位程序。脚本仅用标准库，无需 pip 运行依赖。

```powershell
python skills/doctor-title/scripts/workflow.py preflight
python skills/doctor-title/scripts/workflow.py context
python skills/doctor-title/scripts/workflow.py check --draft .doctor-work/draft.json
python skills/doctor-title/scripts/workflow.py archive --draft .doctor-work/draft.json
```

每条命令均支持 `--skills-root <父目录>`。`context`、`check`、`archive` 支持 `--output-root <目录>`，用于测试或非 Windows 系统；正式运行不要随意更换目录，否则会使用另一份历史。默认通过 Windows Known Folder API 定位当前用户桌面，兼容桌面迁移/OneDrive，不硬编码管理员桌面。

- `preflight`：不生成内容；返回依赖路径，缺项非零退出。
- `context`：首次建立日期基线；输出全部压缩历史、当日阈值和达到上限通知。生成前必须读取。
- `check`：检查草稿内容和相似度，不新增历史；失败返回非零退出。只有标题、分类、摘要、特征时可先检查标题；有正文就计算正文相似度。
- `archive`：执行完整交付校验、二次查重、独占锁、桌面文件写入和正式记忆记录。无图时必须提供完整提示词和真实跳过原因。

输出为 UTF-8 JSON，错误状态不表示成功。脚本不自动调用 Agent 或生图工具；执行顺序由三个 Skill 的指令约束。

## 数据契约

草稿参考 `assets/draft.example.json`；示例缺正文、来源和审核，刻意不能直接正式归档。

| 字段 | 规则 |
| --- | --- |
| `title` | 锁定的原始标题；同一作品的文章与图片含义一致 |
| `category` | `prostate` / `sexual-function` / `reproductive-infection` / `foreskin-phimosis` / `male-infertility` |
| `summary` | 真实压缩摘要，不超过 400 字 |
| `features` | 4 至 8 个不同的具体短语，不包含泛泛大类，不能为过查重随意改写 |
| `article` | 正文 Markdown，不重复一级标题，由归档器加原始标题；不包含提示词与内部记录 |
| `banner_text` | 图片拟显示的全部文案列表；即使跳过图片也应完成文案设计 |
| `palette` | 本次配色名，不超过 120 字；写入压缩历史，下一次先看近期配色再轮换，不固定暗黑或浅蓝 |
| `image_tool` | 实际图像工具名；无工具明确写“无可用生图工具”，不虚构工具 |
| `image_prompt` | 实际准备递交生图工具的完整提示词，包含尺寸、排版、色彩、元素、准确文案与限制；不可只写“生成医疗图” |
| `image_status` | `generated` 或 `skipped` |
| `image_path` | `generated` 时必须是本地 PNG/JPEG/WebP 路径；相对路径相对于草稿文件 |
| `skip_reason` | `skipped` 时说明工具缺失、权限、失败或质量不合格的真实原因 |
| `sources` | 至少一个真实事实来源：每项 `title`、`url`、`claim` 非空；公开 HTTPS 链接，`claim` 说明支持的具体主张；不把文风参考当证据 |
| `loaded_skills` | 已实际读取的三个标准名称 |
| `review` | `medical`、`compliance`、`originality`、`semantic` 须真实审核后为 true；生成图的 `visual` 也必须 true |

不在草稿、图片与记忆中放真实患者姓名、手机号、病历或可识别照片。脚本不能验证 URL 实际支持主张，也不能检出所有药物、设备和图像违规；审核是必需步骤。

图像校验检查真实文件格式、容器边界、尺寸；PNG 额外检查 CRC、解压流和扫描行，JPEG/WebP 不包含完整像素解码器。因此即使程序通过，Agent 仍须实际打开图片确认可显示、文字准确、无脸无品牌且合规，不能仅靠文件头填写 `visual=true`。单张文件不超过 50 MiB，PNG 解压数据不超过 100 MiB。

## 相似度与缩减记忆

日期采用运行电脑的本地日历日，以第一次 `context/check/archive` 的日期为起点。公式为 `min(0.20 + 0.002 × 已过天数, 0.40)`：当天 20%，次日 20.2%，第 100 天达到 40%，之后不再增长。不是每次生成加，也不是每天乘 1.002。时钟早于起点时报错，不改写起点。

每条已交付记录保留标题、短摘要、稳定特征、分类、日期、配色、文件相对路径及以下指纹，而不是再复制整篇文章：

1. 标题：NFKC 规范化、移除标点空白、有限同义归一后，取中文/字母数字二元组，用 SHA-256 截断指纹。计算集合 Jaccard 重合度。
2. 正文：同样归一，保留完整字符二元组的 bottom-128 指纹。比较双方并集的前 128 个指纹，其中共同指纹所占比例估算正文重合度。短文集合不足 128 时为精确 Jaccard。正文只排除统一免责声明，不剔除医学安全警示。
3. 特征：规范化特征短语集合的 Jaccard。共同大类本身不参与相似度。
4. 与每条历史比较后，取标题、正文、特征三个分数的较大者；再取全历史的较大者作为该次分数。必须 `分数 <= 当日阈值`，不以四舍五入后的显示值放行。规范化标题完全相同无条件拒绝。

这不是严格的语义重复率或统计保证，短文本、同义改写及采样均有限制。Agent 必须读取摘要，并回看接近记录正文，检查同主题同结论换皮；不能宣称已证明“语义重复低于20%”。没有可接受候选时停止，不自动删除记忆、降低检查维度或提高阈值。医学准确与急症警示不能为了去重被牺牲。

只有完成正文、合规审核和图片生成/合法跳过并归档成功的作品计入历史。重复提交、候选、失败产物不计入。达到 40% 的命令响应含通知，Agent 必须转告用户；该通知会持续出现，避免无人值守漏报。

## 桌面归档

```text
<真实桌面>/doctorlist/
  img/<安全化标题>.png           # 或 .jpg/.webp，跳过时不伪造此文件
  title/<安全化标题>.md          # 原题 + 正文
  title/<安全化标题>.json        # 原题、来源、审核、提示词、跳过原因及查重报告
  memory/state.json              # 起始日期、算法版本
  memory/history.jsonl           # 每次一条压缩记录
```

初始化记忆会提前创建 `doctorlist/memory`；完成全流程时才创建并写入正式 `img`、`title` 产物。Windows 非法字符与路径分隔符替换为下划线，长标题截短并加哈希；重名加确定性哈希或序号，绝不覆盖旧文件。同一作品的标题、正文、图像和清单使用同一文件名主干，原始标题完整保留。

`.workflow.lock` 防止并发检查提交冲突。写入前建立 `memory/pending.json` 事务标记；普通异常会回滚本次创建文件，进程崩溃留下标记时拒绝后续写入，提示人工核对清单、历史与产物后再恢复，不自动删除未知文件。历史损坏、被删除但仍有已归档文件、版本不符时失败关闭，不当作空白历史。不要手动删除记忆；迁移需连同整个 `doctorlist` 迁移。
