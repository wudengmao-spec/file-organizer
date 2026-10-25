---
name: file-organizer
description: |
  按固定层级约定整理杂乱文件目录（3 层结构 / 第1层≤7 / 第2-3层≤3 / 禁止删除 / 可回滚），
  并在整理前做敏感路径排除与同内容去重。Use this skill when the user asks to
  "整理资料" / "整理文件" / "整理电脑文件" / "整理下载文件夹" / "归置归档" /
  "organize my files" / "tidy up my Downloads" / "sort this folder" / "clean up
  this directory", or hands you a messy directory and asks for a folder structure.
  也用于清理垃圾文件（走暂存区或回收站，可恢复）。
  Do NOT use for: 单个文件的重命名或移动（直接用工具做）; 磁盘空间占用分析
  （用 storage-analyzer）; 代码仓库内部的目录重构（那是软件工程任务，改动会破坏
  构建）; 已有明确分类方案、只需要照着执行的批量搬运。
---

# File Organizer

把一个杂乱目录整理成固定结构的可检索树。

**核心分工：语义判断归你，机械约束归脚本。** 分类轴怎么定、哪个文件属于哪类、
哪些是重复、哪些是垃圾 —— 这些需要理解文件内容，由你判断并写进 `map.json`。
深度、层宽、路径长度、禁删、同名不覆盖、敏感路径 —— 这些不需要判断，由
`scripts/organize.py` 强制校验，不通过就拒绝执行。

你自己写 `map.json`，但**不要自己用 `mv`/`Move-Item` 搬运**。绕过脚本就绕过了
禁删、路径长度、回滚和敏感路径保护。

## Inputs to collect

开工前先确认，缺了就问（问一次，别逐条追问）：

- **目标目录** —— 要整理哪个目录？整理后放在哪？（同目录原地整理 vs 归集到新根）
- **分类轴意图** —— 用户心里按什么分？项目 / 主题 / 类型 / 时间？
  如果用户没说，先盘点再基于证据提 2-3 个候选轴让用户选，别硬套一个模板。
- **要不要清理垃圾** —— 整理和清理是两件事。默认只整理，不删。
- **是否已在用某个既有结构** —— 比如已有 PARA、已有编号桶。沿用它，别另起炉灶。

## Procedure

### 1. 盘点（只读）

```powershell
python <skill_dir>/scripts/organize.py inventory --src "<目标目录>" --out "<工作目录>/inventory.json"
```

读 `inventory.json`，重点看四个字段：

| 字段 | 用途 |
|---|---|
| `top_level` | 每个顶层目录的标题（从 README/AGENTS 首行提取）、文件数、扩展名分布、样例文件名 —— 这是你设计分类轴的**主要证据来源** |
| `ext_distribution` | 全局格式分布 |
| `duplicate_groups` | 同内容重复（size→sha256 两级识别），决定保留哪份 |
| `protected_paths` / `symlinks` / `atomic_bundles` | 已被自动排除，别再试图处理 |

**证据不足就别猜。** 顶层目录没有标题、样例文件名也看不出所以然的，进内容读
前几行（`.md`/`.txt`/`.html` 读头部，PDF 先看文件名）再决定。实在读不出来，
就把该项留在原地列进"待定"，不要塞进某个像样的桶里。

### 2. 设计层级结构

**结构来自证据，不来自模板。** 具体规则见 `references/hierarchy-rules.md`，核心几条：

- **≤3 层，且每层都要有区分度。** 每一层都得让"下一步选择更容易"。如果加一层
  只是为了装下同类的一堆，加这层就没意义。
- **按"你会怎么找"分，不按"它是什么格式"分。** 按扩展名分（`图片/`、`视频/`）
  是最常见的错误分法 —— 用户找文件时想的是"哪个项目的封面图"，不是"哪张是 png"。
  格式只能是**最内层**的区分手段，且要给项目让路。
- **项目目录不拆散。** 一个项目目录（有自己的 README/AGENTS/package.json 等）
  整体归到它该在的位置，别把里面的文件按类型抽出来散落。
- **先给方案，再落地。** 告诉用户你打算建成什么样（树形），确认或修正后再执行。
  用户对结构的偏好是判断题，不是执行题。

### 3. 写映射文件

`map.json` 是你（语义）和脚本（机械）之间的唯一接口：

```json
{
  "src": "D:\\杂乱的下载目录",
  "root": "D:\\归集",
  "max_depth": 3,
  "l1_max": 7,
  "l23_max": 3,
  "moves": [
    { "from": "D:\\杂乱的下载目录\\claude-code-skill", "to": "01-工具/开发/claude-code-skill" },
    { "from": "D:\\杂乱的下载目录\\2024年报.pdf", "to": "02-文档/财务/2024年报.pdf" }
  ]
}
```

`to` 是相对 `root` 的路径，用 `/` 分隔。层级约束由脚本校验，但**你要设计出
一个能过校验的树**：

- 第 1 层最多 7 个桶 —— 超了就说明分类轴太细，合并同义桶
- 第 2/3 层各最多 3 个子项
- **扇出是"逐节点"语义**：约束的是每个目录的直接子项数，不是整层总数。
  根下 4 个桶、每个桶 3 个子项是合法的（`4 ≤ 7`，`3 ≤ 3`）
- `from` 用**绝对路径** —— 脚本按当前工作目录解析相对路径，写相对会搬错文件
- `from` 可以是单个文件，也可以是整个目录（目录整体移动）
- 不要把同一份内容列两次

### 4. 校验（只读，必须先过）

```powershell
python <skill_dir>/scripts/organize.py apply --src "<目标目录>" --map "<工作目录>/map.json"
```

不加 `--apply` 就是纯校验。它会报出：超层数、超层宽、路径超 260 字符、
源不存在、目标自噬、源是敏感路径/符号链接。

**有错就改 `map.json` 重跑，直到零错误。** 不要跳过这步直接 `--apply` ——
脚本本身也会拦，但那时你已经浪费了一次往返。

### 5. 执行

```powershell
python <skill_dir>/scripts/organize.py apply --src "<目标目录>" --map "<工作目录>/map.json" --apply
```

执行后脚本会：逐条移动（同名自动改名不覆盖）、**对账**（文件数与字节数守恒）、
报告源端残留、生成回滚映射表 `map.rollback.json`。

**对账不一致（退出码 4）要当事故处理**：告诉用户实际数字差多少、回滚表在哪，
让用户决定是回滚还是继续。不要自己猜着往下走。

### 6. 对账与回滚

```powershell
python <skill_dir>/scripts/organize.py verify --src "<目标目录>" --map "<工作目录>/map.json"
```

出问题时回滚（**整批回滚，不要挑着回滚** —— 挑着回滚会破坏目录树的一致性）：

```powershell
python <skill_dir>/scripts/organize.py rollback --rollback-map "<工作目录>/map.rollback.json" --apply
```

### 7. 清理垃圾（可选，且是独立步骤）

整理归整理，清理归清理。清理**只在用户明确要求时做**。

```powershell
# 7a. 先看清单（只读）
python <skill_dir>/scripts/organize.py clean --root "<目标根>" --targets "<工作目录>/junk.txt"

# 7b. 移入暂存区（可恢复，用户确认后清空暂存区）
python <skill_dir>/scripts/organize.py clean --root "<目标根>" --targets "<工作目录>/junk.txt" --apply

# 7c. 送系统回收站（仍可恢复）
python <skill_dir>/scripts/organize.py clean --root "<目标根>" --targets "<工作目录>/junk.txt" --apply --recycle
```

`junk.txt` 每行一个相对路径，或写成 JSON 数组。只有高置信度垃圾才进清单：
零字节文件、确认过的孤立安装包、已验证保留副本的精确重复。**有疑问的留在原地。**

## Output contract

整理结束时，给用户：

1. **最终目录树** —— 实际落盘的结构，带每个桶的文件数
2. **数字对账** —— 整理前文件数/字节数 vs 整理后，对上了说对上了
3. **待定清单** —— 没能可靠归类的项，逐条说明为什么待定（这是给用户的决策入口，
   不是你的失败）
4. **回滚表路径** —— `map.rollback.json` 的位置和回滚命令
5. **排除清单** —— 敏感路径、符号链接、原子包各多少项（说明它们被保护而非丢弃）

不要在用户没要求时生成额外文档文件。

## Failure handling

| 症状 | 怎么办 |
|---|---|
| 校验报超层/超层宽 | 合并同义桶后重写 `map.json`。不要放宽 `max_depth`/`l1_max` —— 那是在规避约定，不是解决问题 |
| 路径超 260 字符 | 缩短桶名。本机 `LongPathsEnabled=0`，超长会直接失败。脚本留了 250 上限 |
| 对账不一致（退出码 4） | 报告差异数字和回滚表路径，让用户决定回滚还是接受。**不要自己继续往下走** |
| 回滚时目标已不存在 | 跳过并说明。可能有用户手动移动过 |
| 移动中途失败 | 脚本逐条容错，失败的留在原地并打印。报告哪些成功哪些失败 |
| 盘点时文件被占用 | 该文件跳过即可，不影响整体 |
| 目标根已有同名目录 | 脚本自动改名（`_1`、`_2`）并记录在回滚表。告诉用户被改名的项 |
| 用户中途改主意要换分类轴 | 回滚，再重新设计。不要在已移动的树上二次改道 |

**卡住的信号**：如果盘点完发现无法判断分类轴（比如顶层 30 个目录、每个都没线索、
总量又大），停下来把证据摘要给用户，让他给一个分类方向。硬猜出来的结构
比不整理更糟 —— 用户会在一个错的树上继续堆东西。

## Examples

**Input**: 「帮我整理一下 D:\杂乱的下载目录」

盘点 → 顶层有 12 个目录（`claude-code-skill`/`2024年报.pdf`/`截图`/`安装包`…），
其中 `claude-code-skill` 有自己的 README 且 `title` 显示是工具类项目。

分类轴候选：**按"这是什么"分**（工具/文档/素材）而不是**按"什么格式"分**（pdf/png/zip）
—— 因为用户找文件时想的是"哪个项目的封面图"。

产出结构（第 1 层 3 个桶，第 2 层各 ≤3，第 3 层 ≤3）：

```
归集/
├── 01-工具/开发/claude-code-skill
├── 02-文档/财务/2024年报.pdf
└── 03-素材/截图/
```

`截图/` 只有一层，因为按项目归类需要读内容才知道属于谁，读不出来就留在原地列进
待定清单。

---

**Input**: 「D:\下载 里全是垃圾，清一下」

先盘点看重复组和零字节文件，只把**高置信度**的写进 `junk.txt`：

```
安装包/setup_v3.exe
旧版本/setup_v2.exe
```

`setup_v3.exe` 和 `setup_v2.exe` 大小相同但哈希不同 —— 不是重复，是不同版本，
需要问用户保留哪个，别自己挑。

清理走 `clean --apply`（进暂存区），而不是直接送回收站 —— 暂存区让用户自己
看一眼再清。

## Windows (win32) platform notes

- **脚本路径**：下面的 `<skill_dir>` 指本 skill 所在目录，即
  `C:\Users\Lenovo\.minimax\skills\file-organizer`。
  拿不准就先用 `Get-ChildItem -Path C:\Users\Lenovo\.minimax\skills -Recurse -Filter organize.py`
  定位，确认后再调用 —— 别凭记忆拼路径。
- 脚本用 `python` 调用（本机 Python 3.14.7 可用，`python3` 在 Windows 上不存在）。
- 路径含空格或中文时用双引号包裹参数。
- 回收站调用走 `Microsoft.VisualBasic.FileIO`，脚本已封装；无回收站机制时
  `clean --recycle` 会把该项留在原地并报告，不会硬删。
- 本机 `LongPathsEnabled=0`，目标路径上限 250 字符（脚本默认值）。
- 符号链接、原子包（`.app`/`.photoslibrary`）、敏感路径由脚本在盘点阶段自动
  排除并记录，不需要你手工处理。

## Reference files

- `references/hierarchy-rules.md` —— 层级设计规则详解（≤3 层的判断、项目原子性、
  重复类别检查、跨域 tie-break、命名）
- `references/safety-rules.md` —— 安全边界详解（敏感路径清单、删除阶梯、
  符号链接、原子包、去重、冲突策略、回滚契约）
- `references/mapping-schema.md` —— `map.json` / `inventory.json` 完整字段说明
