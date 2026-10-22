# doctorTitle-skill

医疗公众号三件套的标题生成与流程协调仓库。配套仓库是 `doctorArticle-skill`、`doctorImg-skill`，缺任何一项都不独立运行。

## 结构

实际标准 Skill 根目录是 `skills/doctor-title/`，包含 `SKILL.md`、`references/`、`assets/`、`scripts/`。仓库名保留用户指定大小写；官方规范的 Skill 名和父目录使用小写。标准依据：https://agentskills.io/specification 。

将三个仓库的内层 Skill 目录一起安装到宿主支持的技能目录，或注册三个仓库的 `skills/` 路径。在 Kilo 中可把这些路径加入现有配置的 `skills.paths`；合并已有配置，不能覆盖其他设置。本仓库不会自动修改全局配置。

默认本地布局：

```text
C:\demo\doctorTitle-skill\skills\doctor-title
C:\demo\doctorArticle-skill\skills\doctor-article
C:\demo\doctorImg-skill\skills\doctor-img
```

在支持 Skill 的 Agent 中请求“加载 doctor-title、doctor-article、doctor-img，生成一套男性健康公众号图文”，即可由标题 Skill 协调。也可以让 Agent 直接读取三个 `SKILL.md`。脚本不是独立的内容生成模型。

## 运行与验证

需要 Python 3.11+，运行代码无第三方依赖。从本仓库执行：

```powershell
python skills/doctor-title/scripts/workflow.py preflight
python skills/doctor-title/scripts/workflow.py context
python -m unittest discover -s tests -v
```

完整操作、JSON 字段、每日阈值、崩溃恢复说明见 [工作流](skills/doctor-title/references/workflow.md)。其他系统需通过 `--output-root` 明确桌面归档路径。

可另行安装官方参考验证器，对三个实际 Skill 目录执行 `skills-ref validate <skill目录>`。不要把仓库根目录作为校验对象。

## 注意

合规词表只是辅助，不替代医学、语义、图像及平台审核。已采用样例的传播结构，未沿用其中的疗效承诺。相似度为明确文本算法与 Agent 语义复核组合，不保证“爆款”或语义检测准确率。参考图片不入库；正式作品和用户记忆只保存在用户桌面，不上传 Git。

远程仓库：https://github.com/rontowerdragonsingingZi/doctorTitle-skill.git 。Git 远程配置不等于内容已提交或已推送。
