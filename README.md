# codex-optimizer

一份给 Codex（GPT 系列模型）用的全局 `AGENTS.md`，让它的回答和动手习惯更接近 Claude：少一点防御和报告腔，判断直接，操作不搞仪式；同时保留它做代码评审时的严谨。附带一套用全新 Codex 实例对比提示词效果的测试工具，以及从自己的 Claude Code / Codex 记录里抽取语料的脚本。

提示词是中文写的，针对中文回复调过。

## 提示词

[`prompt/AGENTS.md`](prompt/AGENTS.md) 放到 Codex 的全局位置即可生效：

```sh
cp prompt/AGENTS.md ~/.codex/AGENTS.md
# 确认已进入模型输入（应输出 True）
codex debug prompt-input | python3 -c "import sys; print('和用户一起把事情做成' in sys.stdin.read())"
```

新开的会话才会读到它。主要内容：

- **判断和质疑**：第一句回答用户问的事；拿不准时先说最可能的情况，不确定只说一次，并说成下一步怎么查；被问"你确定吗"时去查决定结论的那一环，查完再说，不因为被追问就改口。
- **说话方式**：不替用户设想一个推论再去否认（"这不代表……""不能直接当成……"）；讲事情本身，不讲依据强弱和判断变没变；用口语词（用、所以、现在、还没、跑、删掉……）；粗体只用在行首标签上；建议直接说做什么，不包装成"下一步最有价值的是……""真正要处理的是……"，说完也不补"这样能……"推销；没验证的事只在影响结论的那句里说一次；结尾要用户动手就直接对他说做什么，或者交给他拍板，不复述结论，不加"未修改/未提交"这类声明。
- **一轮什么时候结束**：事情做完、要用户拍板或授权、剩下的活全卡在只有用户能给的信息上、或者确实卡住时才结束；不写一句“接下来先查……”就停。gpt-6-astra 在用户回答了它的问题之后特别容易这样停，不装提示词也一样。
- **过程消息**：从刚看到的东西说起，再接下一步，不写"我先核对 X，确认是 A 还是 B，避免……"这种调查计划。
- **动手操作**：用操作本身的结果确认成功；不给文件算哈希、不逐个回读比对、不给 git 里或能重新生成的东西做备份；代码里不包 try/except 吞错，不为想象中的输入加检查。
- **评审**：覆盖面不打折，并发与内存序、锁和生命周期、错误路径、部分完成、边界条件照常查到底；上面的"少加检查"只针对自己写的代码和操作。

## 效果

在 gpt-6-astra（medium）上，用全新 Codex 实例对比"不装提示词"和"装 `prompt/AGENTS.md`"：

| | 不装 | 装上 | Claude 的历史回复 |
|---|---|---|---|
| 联网调研回复里出现拦截句（不能直接/不代表/准确说） | 100% | 17% | 14% |
| 整句加粗 | 100% | 17% | 3% |
| 口语词对占比（用/使用、所以/因此、现在/目前……） | 45% | 97% | 90% |
| 结尾是总结 | 33% | 8% | 3% |
| 替换文件任务里算哈希、做备份（多出的文件数） | 每次都有（8 个） | 没有（0） | |

评审能力没有因此下降：5 道真实项目里的并发和时序评审（只读克隆到当时评审的提交，用原评审提示词、max 强度重跑，每题 2 次），装上提示词的 10 次都找到了当年评审发现的问题，不装的漏了 3 次。样本小，能说明的是没有变差。

已知做不到的：联网回答的出处没法集中放到末尾，Codex 联网工具的定义要求引用紧贴论断，`AGENTS.md` 盖不过。

## 测试工具

需要 Python 3.11+ 和 `codex` CLI。工具只读取你的 `~/.codex`（模型、provider、登录、模型缓存），每次运行在 `.work/homes/` 下新建独立的 CODEX_HOME，把被测提示词写成它的 `AGENTS.md`。

```sh
# 两组对比：default 是空 AGENTS.md，agents 是本仓库的提示词；每个场景跑 2 次
python3 eval/run.py default,agents=prompt/AGENTS.md --reps 2

# 操作、写代码、评审三类场景的行为分数
python3 eval/score_behavior.py default,agents --dump runs/behavior.txt

# 回复风格指标；--baseline 可以给一份参考回复（见下面的语料分析）
python3 eval/score_style.py default,agents --baseline claude.json --dump runs/style.txt
```

[`eval/scenarios.example.json`](eval/scenarios.example.json) 里是 7 个合成场景：在 git 仓库里合并配置、替换文件、批量改名（看有没有多余的备份、哈希、回读），两道写代码题（隐藏测试检查短写、零进展、错误传播等），两段埋了并发 bug 的 C++ 代码（看评审能不能找到）。场景字段：

| 字段 | 含义 |
|---|---|
| `id`, `user` | 必填；`id` 前缀 `b_`/`c_`/`r_` 决定 `score_behavior.py` 的分类 |
| `followup` | 第二轮用户消息，用 `codex exec resume --last` 发出，适合测"你确定吗？" |
| `cwd` | 在已有目录里跑（只读沙箱），比如真实仓库的只读克隆；不填则新建空目录 |
| `files`, `git`, `check` | 在新目录里放夹具文件（可提交成 git 仓库），跑完执行 `check` 判结果 |
| `web` | 打开联网搜索，shell 也可联网 |
| `effort`, `timeout` | 单个场景的推理强度和超时秒数 |

自己的真实场景可以另写一个 JSON，用 `--scenarios` 指定。

## 语料分析

从本机记录里抽取对话和操作，用来找你自己用过的两种模型之间的差异：

```sh
python3 analysis/extract_turns.py > turns.jsonl      # 每轮：用户原话、助手最终回复、用到的工具、模型
python3 analysis/extract_actions.py > actions.jsonl  # 执行过的命令、写入的代码
# 记录不在默认位置时：--claude-dir DIR（可重复）、--codex-dir DIR
```

`extract_turns.py` 按模型分组（Claude 模型 / Claude Code 里接的其他模型 / Codex），把 Claude 那组的 `final` 存成 JSON 列表，就能直接给 `score_style.py --baseline` 用。

## 许可

[MIT](LICENSE)
