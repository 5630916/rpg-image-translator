# rpg-image-translator

把 **RPG Maker MV/MZ 游戏里的日文图片**批量翻译成中文，并**按游戏原格式加密回去**。

面向场景：游戏用图片承载对话/帖文/UI 文本（而非 `data/*.json`），
想在不改游戏程序的前提下做中文化。

- **OCR 走系统自带**（Windows.Media.Ocr）——不联网、不花钱
- **翻译走 DeepSeek API**——成本极低（本项目实测约 **0.3 元 / 177 张图**）
- **宽度自适配**：中文比日文宽，脚本会按原图位置和可用空间排版
- **保留原图画质**：同尺寸、同色彩模式、保留透明与圆角，不重绘背景

> ⚠️ **仅供个人学习与自用汉化。** 请勿分发游戏素材（原图、译文图、加密件均为游戏衍生内容）。
> 本仓库只包含脚本，不含任何游戏资源。

---

## 目录

- [工作原理](#工作原理)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置密钥](#配置密钥)
- [快速开始](#快速开始)
- [两条流水线怎么选](#两条流水线怎么选)
- [命令详解](#命令详解)
- [输出规格](#输出规格)
- [装回游戏](#装回游戏)
- [常见问题](#常见问题)
- [踩坑记录（重要）](#踩坑记录重要)
- [仓库结构](#仓库结构)

---

## 工作原理

```
游戏 img\pictures\<目录>\*.png_        ← RPG Maker 加密格式
        │
        │  ① 解密（用 data/System.json 里的 encryptionKey）
        ▼
   原图\*.png                          ← 明文 PNG，与游戏内显示一致
        │
        │  ② OCR（Windows.Media.Ocr，日文引擎）
        ▼
   _ocr\*.txt                          ← 逐条文本（对话类）或逐行+坐标（排版类）
        │
        │  ③ 翻译（DeepSeek API，带磁盘缓存，重复内容不重复付费）
        ▼
   译文（内存 / 缓存）
        │
        │  ④ 渲染（抹掉原文 → 同位置画中文，字号/宽度自适应）
        ▼
   成品\*.png                          ← 与原图同尺寸、同模式
        │
        │  ⑤ 回加密
        ▼
   加密件\*.png_                       ← 可直接覆盖进游戏
```

关键点：**④ 抹掉原文**这一步最容易出问题。RPG Maker 的图片往往背景透明、
文字直接画在透明画布上（图内根本没有"纸面"颜色），
所以底色不能靠"取最常见的颜色"猜——详见 [踩坑记录](#踩坑记录重要)。

---

## 环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | **Windows 10 / 11** | OCR 依赖 WinRT，macOS/Linux 不适用 |
| Python | **3.10+** | 开发环境为 3.12 |
| Python 依赖 | `Pillow`、`numpy` | 其余全是标准库 |
| Windows OCR 语言 | **日语** | 设置 → 时间和语言 → 语言和区域 → 添加"日语" |
| 字体 | 微软雅黑（`msyh.ttc`） | Windows 自带，用于渲染中文 |
| DeepSeek API key | 需要一个 | [platform.deepseek.com](https://platform.deepseek.com/) 申请 |

**先跑环境自检**，它会逐项告诉你缺什么、怎么补：

```console
python envcheck.py
python envcheck.py --encoding-test     # 附带做一次真实 OCR 往返测试
```

典型输出：

```
[OK]   操作系统            win32
[OK]   Python 版本         3.12.14
[OK]   Pillow              PIL 12.3.0
[OK]   numpy               numpy 2.3.5
[OK]   微软雅黑 msyh.ttc      C:\Windows\Fonts\msyh.ttc
[OK]   Windows PowerShell  C:\Windows\System32\...\powershell.exe
[OK]   Windows OCR 引擎     已装语言: ja, zh-Hans-CN
[OK]   OCR 语言 ja-JP       可用
[OK]   DeepSeek API key    来自 ~\.dsh\deepseek-api-key
结论: 环境就绪，可以开始翻译。
```

> **最常见的卡点**：没装日语 OCR 语言包。此时自检会报
> `OCR 语言 ja-JP 不可用`，去「设置 → 时间和语言 → 语言」添加日语即可
> （只需"基本输入/语言包"，不必设为首选语言）。

---

## 安装

本仓库无第三方可执行文件，克隆后装两个 Python 包即可：

```console
git clone <本仓库地址>
cd rpg-image-translator
python -m pip install Pillow numpy
python envcheck.py
```

---

## 配置密钥

脚本按以下顺序找 key，**任一即可**：

1. 环境变量 `DEEPSEEK_API_KEY`
2. 文件 `~/.dsh/deepseek-api-key`（内容就是 key 本身，无换行即可）

```console
:: 方式一：当前会话临时设置
set DEEPSEEK_API_KEY=sk-xxxxxxxx

:: 方式二（推荐，持久）
mkdir "%USERPROFILE%\.dsh"
echo sk-xxxxxxxx> "%USERPROFILE%\.dsh\deepseek-api-key"
```

PowerShell：

```powershell
# 方式一：当前会话
$env:DEEPSEEK_API_KEY = "sk-xxxxxxxx"

# 方式二（推荐，持久）
New-Item -ItemType Directory -Force "$HOME\.dsh" | Out-Null
Set-Content "$HOME\.dsh\deepseek-api-key" "sk-xxxxxxxx" -NoNewline
```

> 翻译结果会缓存到 `~/.dsh/translation-cache.json`。
> 同一句话第二次遇到**不再调用 API**，所以重复跑、换渲染参数重跑都几乎不花钱。

---

## 快速开始

以一次典型的「解密 → OCR → 翻译 → 渲染 → 加密」为例：

```console
:: 0) 自检
python envcheck.py

:: 1) 解密：把游戏里的加密图片转成明文 PNG
python rpg_decrypt.py --dir "D:\游戏\img\pictures\sino_rime_ntr" ^
                      --out "译图\sino_rime_ntr\原图" ^
                      --key-from "D:\游戏"

:: 2) OCR：产出逐条文本
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\win-ocr.ps1 ^
           -Path "译图\sino_rime_ntr\原图" ^
           -Out  "译图\sino_rime_ntr\_ocr\zh.txt" ^
           -Lang ja-JP -Mode full

:: 3) 翻译 + 渲染（对话气泡类）
python pipeline.py --ocr "译图\sino_rime_ntr\_ocr\zh.txt" ^
                   --out-dir "译图\sino_rime_ntr\成品" ^
                   --fit-source "译图\sino_rime_ntr\原图" ^
                   --keep-box --match-source-ink

:: 4) 回加密
python rpg_decrypt.py --encrypt --dir "译图\sino_rime_ntr\成品" ^
                      --out "译图\sino_rime_ntr\加密件" ^
                      --key-from "D:\游戏"

:: 5) 校验（加密件与成品是否逐字节一致、尺寸是否对）
python verify-all.py --game "D:\游戏" --batch-root "译图"

:: 6) 装回游戏（先干跑看清单，再去掉 -CheckOnly）
powershell -NoProfile -ExecutionPolicy Bypass -File install-to-game.ps1 ^
           -Game "D:\游戏" -SrcRoot "译图" -CheckOnly
```

**排版类图片（SNS 帖文 / 状态面板）** 用另一条流水线，它自动串起上面全部步骤：

```console
python sns_pipeline.py --dir "译图\ayano_sns" --game "D:\游戏"
```

---

## 两条流水线怎么选

先判断你的图片属于哪一类——**选错了排版会乱**。

| 类型 | 长什么样 | 用什么 | 判据 |
|---|---|---|---|
| **对话气泡** | 一条对话框，文字居左或居中；常见 321×52 | `pipeline.py`（或 `run-all.py` 批量） | 文字基本在一个气泡内，行数少 |
| **多行排版** | UI 截图：昵称/handle/正文/图标各占一处；常见 365×265 | `sns_pipeline.py` | 一张图里有**多处**独立文本块 |

判断技巧：打开一张图看文字是不是**分布在画面各处**（比如上方昵称、下方正文、
右下角还有操作图标）——是就用 `sns_pipeline.py`。

`pipeline.py` 把整段文字**居中画进一个框**；`sns_pipeline.py` 按 OCR 找到的
**每一行各自的原坐标**替换。用错了就会把 SNS 帖压成一坨居中文字。

---

## 命令详解

### `envcheck.py` — 环境自检

| 参数 | 说明 |
|---|---|
| `--lang ja-JP` | 指定要检查的 OCR 语言标签，可重复 |
| `--encoding-test` | 额外生成一张测试图做真实 OCR 往返 |

### `rpg_decrypt.py` — 解密 / 加密

| 参数 | 说明 |
|---|---|
| `--dir` | 必填。输入目录（解密时是加密图片目录，加密时是 PNG 目录） |
| `--out` | 输出目录。默认 `<输入>_decrypted` / `<输入>_encrypted` |
| `--key-from` | 必填。游戏根目录（需含 `data/System.json`） |
| `--encrypt` | 改为加密（PNG → `.png_`） |
| `--batch` | 遍历 `--dir` 下所有含加密文件的子目录 |
| `--dry-run` | 只统计不写文件 |

> 也支持 `.rpgmvp`（MV）与 `.png_`（MZ）。密钥从 `System.json` 的
> `encryptionKey` 自动解析，不用手抄。

### `pipeline.py` — 对话气泡：翻译 + 渲染

| 参数 | 说明 |
|---|---|
| `--ocr` | OCR 产物文本 |
| `--out-dir` | 输出目录（与 `--ocr` 搭配） |
| `--root` / `--out-root` | 批量模式：遍历根目录下每个子目录当独立主题 |
| `--fit-source DIR` | **按源图尺寸输出**（与源图同宽高、同色彩模式） |
| `--keep-box` | **保留原图底色/透明/圆角**（`--fit-source` 必配） |
| `--match-source-ink` | 让译文墨迹高度贴近原日文（观感更统一） |
| `--text-color RRGGBB` | 强制文字色；默认**从原图自动提取** |
| `--limit N` | 只处理前 N 条（试跑用） |
| `--model NAME` | 覆盖翻译模型 |

**推荐组合**（与源图同规格、保留背景与透明）：

```console
python pipeline.py --ocr <ocr.txt> --out-dir <成品> ^
                   --fit-source <原图> --keep-box --match-source-ink
```

### `sns_pipeline.py` — 多行排版：全自动流水线

| 参数 | 说明 |
|---|---|
| `--dir` | 必填。工作目录（需含 `原图\`） |
| `--game` | 游戏根目录；给了就顺带回加密 |
| `--skip-ocr` | 复用已有坐标 OCR（改过渲染逻辑后重跑用，省时间） |
| `--limit N` | 只处理前 N 张（试跑用） |
| `--batch N` | 每次请求翻译多少行（默认 20） |

它会依次执行：合成白底 → 批量坐标 OCR → 逐行分类（昵称/@handle/编号自动跳过）
→ 分批翻译 → 逐行原位渲染 → 回加密。

### `run-all.py` — 对话气泡：批量 + 语言自动判定

| 参数 | 说明 |
|---|---|
| `--root` | 含多个主题子目录的根目录，每个子目录当独立主题 |
| `--dir` / `--out-dir` | 单目录模式 |
| `--lang` | 强制 OCR 语言（默认自动判定 zh-Hans-CN / ja-JP） |
| `--skip-ocr` | 跳过 OCR，复用已有产物 |

亮点：会**同时用两个 OCR 引擎跑**，再按「是否像合法日语」挑结果——
因为 Windows OCR 引擎错配时会产出逐字乱码（详见踩坑记录）。

### `verify-all.py` / `verify-installed.py` — 校验

```console
:: 校验工作区里的加密件
python verify-all.py --game "D:\游戏" --batch-root "译图"

:: 校验游戏里已安装的文件（从游戏目录读回、解密、与成品比对）
python verify-installed.py --game "D:\游戏" --batch-root "译图"
```

`verify-installed.py` 会自动发现所有含 `加密件\` 的批次，并假设
**批次名与游戏内目录名相同**；不一致时用 `--map 批次=游戏目录`（可重复）指定。

### `install-to-game.ps1` — 装回游戏

**先改脚本顶部的 `$Game` 与 `$Map`**，或运行时用 `-Game` / `-SrcRoot` 覆盖。

```powershell
# 只预检（不写入）—— 强烈建议先跑这个
.\install-to-game.ps1 -Game "D:\游戏" -SrcRoot "译图" -CheckOnly

# 正式替换（会交互确认）
.\install-to-game.ps1 -Game "D:\游戏" -SrcRoot "译图"

# 替换前把游戏原件另存一份
.\install-to-game.ps1 -Game "D:\游戏" -Backup
```

四重保护：预检不过就中止（不做部分替换）、逐文件核对同名原件（防装错目录）、
`-CheckOnly` 干跑、`-Backup` 备份原件。

---

## 输出规格

每个批次目录的约定：

```
译图\<批次>\
├── 原图\       解密出来的原始 PNG（与游戏内显示一致）
├── _ocr\       OCR 产物文本
├── 成品\       译文图（与原图同尺寸、同色彩模式）
└── 加密件\     回加密后的 .png_（可直接覆盖进游戏）
```

渲染遵循的规则：

| 规则 | 说明 |
|---|---|
| 尺寸 / 色彩模式 | 与源图**逐字节相同**的宽高与 RGBA 模式 |
| 透明与圆角 | 保留（只改文字像素，不动背景） |
| 文字色 | 默认从原图提取：白底取深色、蓝底取白色 |
| 字号 | 按行高中位数统一，并设 14px 下限 |
| 超宽处理 | 先按碰撞避让缩小字号，仍放不下才水平压缩 |
| 头像 / 立绘 | 自动避让，不覆盖 |

---

## 装回游戏

1. **先备份游戏目录**（脚本的 `-Backup` 或手动复制 `img\pictures\<目录>\`）
2. 干跑看清单：`-CheckOnly`
3. 正式替换
4. **回读校验**：`python verify-installed.py --game "D:\游戏"`
5. **进游戏实测**：位置、宽度、字色是否正常

> 有些图片**本来就是纯插画、没有文字**，OCR 会返回 0 行、不生成成品。
> 这是**正常行为**，不是失败——那些图保持原样不动。

---

## 常见问题

**Q: OCR 结果是乱码（像 `匕口亻丿 A 「 乙丸時間扌 ℃ 残`）**
引擎选错了。合法日语一定含**平假名**（の、は、を 等语法助词）；
乱码会堆生僻片假名、几乎没有平假名。`run-all.py` 内置了这个判据会自动挑对的，
单独用 `win-ocr.ps1` 时要自己指定 `-Lang ja-JP`。

**Q: 译文和日文叠在一起（双重文字）**
底色判断失败导致原文没被抹掉。这是本流程最难的坑，见下方踩坑记录。
检查你的图是否属于「透明底 + 深色字」——这类图**图内没有纸面颜色**，
脚本会假定浅色卡片底。

**Q: 中文超出对话框 / 压到旁边元素**
中文比日文宽是常态。加 `--match-source-ink` 让字号贴近原文；
对话类再配合 `--keep-box`。

**Q: `找不到 API key`**
按[配置密钥](#配置密钥)设置环境变量或写文件。

**Q: 翻译结果里有日文残留（如 `……别在意哦？` 前面多个点）**
原图的项目符号被 OCR 读成了标点。脚本的 `clean()` 会剥掉**行首**的孤立标点，
但 OCR 误读偶尔更复杂——这类需要人工抽查。

**Q: 花了多少 token / 多少钱？**
翻译结果按内容缓存，重复内容不重复计费。参考量级：
**177 张图 ≈ 7.9 万 token ≈ 0.3 元**（空闲时段）。

---

## 质量把关：脚本跑通 ≠ 结果可用

**这套脚本不依赖任何 AI agent 运行**——`git clone` 之后照 README 敲命令即可，
OCR 用系统自带、翻译直连 API、渲染纯本地。但要说清楚一件事：

> **脚本保证"流程跑完"，不保证"每张图都对"。** 批量翻译必然有一小部分需要人工过一眼。

实际项目中（同一批约 1600 张图）需要人工介入的比例约 **3~5%**，集中在以下几类。
**建议翻译完先按这几条自查一遍再装进游戏。**

### 必查的四类问题

**① OCR 误读导致译文错**
OCR 会把形近字读错，翻译层看不出问题，但意思变了：

| 原图真值 | OCR 读成 | 译文（错） | 应为 |
|---|---|---|---|
| `まさに` | `まさ纛` | 正纛 | 正是 |
| `パストアップ` | `パストアップ`（半角混入） | Past Up | 半身像 |
| `りカ` | `りカ` | 里卡 | 里香 |
| `ううん` | `つつん` | つつん | 不，抱歉 |

**查法**：把成品图与 `原图\` 并排看，重点核对短句和含人名/数字的行。

**② 输入是垃圾时模型会"编造"**
OCR 若返回乱码，模型可能凭空生成一整句与图片无关的话。实测出现过一次：
OCR 得到 `って・・黯泉・`（真值是 `って…温泉…`），译文却是
一段完全无关的长句——**这类必须删掉重译**。

**查法**：扫一遍译文，找**明显过长**或与上下文不搭的条目。

**③ 日文残留 / 双重文字**
见[踩坑记录](#踩坑记录重要)第 1 条。虽然已修复主要成因，
但个别图仍可能有残留。

**查法**：放大看成品是否出现"两种文字叠在一起"。

**④ 同一人名前后不一致**
同一个角色在不同图里可能被译成不同写法（如 `綾乃` 被译成「绫乃」和「凌乃」）。
**解决方案**：`sns_pipeline.py` 里有一个 `GLOSSARY` 术语表，
把你的角色名对照写进去，翻译时就会统一：

```python
GLOSSARY = {
    "詩乃": "诗乃", "シノ": "诗乃",
    "綾乃": "绫乃", "あやの": "绫乃",
    "和佳": "和佳",
}
```

改完术语表后**必须清缓存**（否则旧译文会被复用），或按
[踩坑记录](#踩坑记录重要)第 8 条做缓存键迁移。

### 建议的工作节奏

```
1. 先拿 5~10 张试跑（pipeline.py --limit 10），确认版面/字号/字色正常
2. 全量翻译
3. 抽查 10%（重点看短句、含人名的行、超长译文）
4. python verify-all.py 校验完整性（加密/尺寸/透明）
5. install-to-game.ps1 -CheckOnly 干跑看清单
6. 正式替换 + verify-installed.py 回读校验
7. 进游戏实测几个点位
```

第 4 步之后的所有问题都还能低成本修（缓存命中，重跑不额外花钱）。

### 已知的脚本层限制

| 限制 | 说明 |
|---|---|
| 仅支持 **日 → 中** | OCR 语言与提示词都按日文设计；换语种需改 `sns_pipeline.py` 的 `SYS_PROMPT` 与 OCR `-Lang` |
| `sns_pipeline.py` 的 OCR 语言写死 `ja-JP` | 见上一行；如需改语言要动代码 |
| 纯插画图不产出 | OCR 返回 0 行即跳过，属正常行为 |
| 不做自动 QA | 上述四类问题需人工抽查 |

---

## 踩坑记录（重要）

这部分是本项目最有价值的部分——都是实际踩过并已修复的，改代码前请先读。

### 1. 透明底图片的"底色"根本不存在于图内

RPG Maker 的 UI 图常是**透明画布上画深色字**。实测某图：

```
整图 84.7% 是透明；不透明像素里 黑字 42.7% + 灰 30% + 近白仅 6.3%
```

也就是说**图内没有"纸面"**——纸面是游戏运行时画的卡片底。
更麻烦的是 Windows OCR 给的**行盒是穿过文字的横条**，盒内可能 **100% 是文字像素**：

```
若按「盒内最常见的颜色」当底色  →  取到黑色（文字色）
→ 抹除时"与底色不同"的像素数 = 0  →  什么都没抹，黑字压在日文上
```

**解法**：用「**最长横向连续段**」区分底色与文字——底色连成一大片，
文字是断开的笔画。

```python
solid_limit = max(6, int(box_w * 0.25))
solid = [(k, n) for k, n in cnt.items() if rowruns.get(k, 0) >= solid_limit]
if solid:   bg = 像素最多的成片色      # 彩色药丸/气泡类
else:       bg = (255, 255, 255)      # 图内无纸面 → 假定浅色卡片底
```

不需要任何亮度/众数假设，两类素材都判对。

### 2. 一个标签页里可以有多种底色

状态面板常见**蓝、粉药丸交替**。若全图只取一个底色，
会把蓝色药丸当成"文字色"，抹除时把整条药丸涂成粉色。

**解法**：**逐行**局部取色。

### 3. 抹除要保留 alpha，且不能用"颜色近似"

- 只抹 `alpha>128` 的像素 → 抗锯齿边缘残留，形成叠影
- 把 alpha 一并写成 255 → 原本透明的区域变不透明，**贴回游戏会多出一块白底**
  （实测不透明占比 4.1% → 25.1%）

**解法**：只改 RGB、保留原 alpha；并且**填回精确色**——
按 `//8*8` 量化统计可以，但填回时要用**档内出现最多的精确色**，
否则会把 `(254,120,197)` 写成 `(248,120,192)`，在纯色药丸上留下肉眼可见的色块。

### 4. 相邻行的包围盒会重叠，渲染必须分两遍

Windows OCR 会把日文逗号「、」的行高抬高（CJK 标点居中于行中），
导致相邻行包围盒互相咬合（实测三行是 `159-176 / 174-192 / 189-204`）。

若按「每行先抹后画」循环，后处理的行在抹除时会**擦掉先处理的行已画好的新文字**
→ 残缺叠影。

**解法**：**第一遍抹掉所有行的原文，第二遍统一绘制所有译文**。
另外同一张图要用**统一字号**（取行高中位数），并**行顶锚定**而非盒内居中。

### 5. 同一视觉行会被 OCR 拆成多段

Windows OCR 常把一句话按列切成多段，且它们 **y 完全相同、x 不同**。

**解法**：y 区间重叠超过一半、且 x 区间不重叠 → 视为同一行，合并后再排版。

### 6. OCR 行盒会漏掉装饰性笔画

原图 `末を——見逃すな`，那条破折号横跨两段之间但**不在任何词框内**，
抹除时被漏掉，译文就压在残留的破折号上。

**解法**：水平吸附——从行框向左右延伸，非底色像素横向间隙 ≤ 3px 就继续吃进去，
遇到较大空白即停（这样不会碰到分隔线和交互图标）。

### 7. PowerShell 5.1 的两个经典坑

- **脚本含中文必须存成 UTF-8 带 BOM**，否则 PS 5.1 按 GBK 解析 → 满屏 `Unexpected token`
- **控制台默认 GBK**，中文输出乱码

> 若用 PowerShell 7，这两点都不再是问题（默认 UTF-8）。
> 本项目的 OCR 脚本必须用 **Windows PowerShell**（WinRT 不认 pwsh），
> 所以脚本已写成 ASCII 安全的形式。

### 8. 缓存键必须与清洗规则一致

翻译缓存按**清洗后的原文**做键。一旦改动清洗规则（比如新增"剥掉行首标点"），
旧键就全部失配 → **全量重翻**（实测白花 13 万 token）。

**解法**：改清洗规则后**先迁移缓存键**（把旧键重映射到新键），再重跑。

---

## 仓库结构

```
rpg-image-translator/
├── envcheck.py            环境自检（先跑这个）
├── rpg_decrypt.py         解密 / 加密
├── pipeline.py            对话气泡：翻译 + 渲染（核心渲染逻辑）
├── render_lines.py        多行排版：逐行原位渲染
├── sns_pipeline.py        多行排版：全自动流水线
├── run-all.py             对话气泡：批量 + 语言自动判定
├── verify-all.py          工作区校验
├── verify-installed.py    安装后回读校验
├── install-to-game.ps1    装回游戏（预检/干跑/备份）
├── winshell.py            Windows 环境适配（PowerShell 定位等）
└── scripts/
    ├── win-ocr.ps1        基于 Windows.Media.Ocr 的 OCR（全文）
    ├── ocr-boxes.ps1      带坐标的单图 OCR
    └── ocr-boxes-batch.ps1 带坐标的批量 OCR
```

模块依赖：`sns_pipeline` → `render_lines` + `pipeline`；
`run-all` / `verify-*` → `rpg_decrypt`；全部经 `winshell` 定位 PowerShell。

---

## 许可

脚本以 MIT 许可提供。**不包含**任何游戏素材、字体或第三方可执行文件。
游戏内容的版权归其各自权利人所有。
