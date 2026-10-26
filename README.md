# 🔬 Project Amadeus — 牧瀬紅莉栖 Discord Bot

基于大语言模型的 **《命运石之门》Amadeus「紅莉栖」** Discord 机器人。她不是冷冰冰的工具人，而是群里的傲娇科学家群友。

---

## 功能一览

| 模块 | 功能 |
|---|---|
| 🎭 **角色扮演** | 完整的牧瀬紅莉栖人格（傲娇科学家 + 网瘾少女 + 心灵导师） |
| 🌏 **世界线系统** | Beta / Alpha / Special 三模式，含命运石之门 1.048596% 彩蛋 |
| 📨 **D-Mail** | `/dmail` 发送时空邮件，20% 概率触发蝴蝶效应改变世界线 |
| 🌀 **世界线收束** | 每日 25% 概率进入收束期，期间 D-Mail 全部失效 |
| 🚆 **PTV 交通** | `/ptv_next` 查墨尔本班次，`/ptv_status` 查线路中断 |
| ⏰ **通勤订阅** | `/ptv_subscribe` 每天定时检查，延误/公交替代时主动 @ 你 |
| 🔍 **联网搜索** | `/search` Tavily 搜索 + LLM 可在聊天中自主决定搜索 |
| 💝 **好感度** | 按用户互动次数分三级态度（stranger → acquainted → familiar） |
| 🖼️ **立绘系统** | `/art_channel` 指定频道，bot 回复自动附带紅莉栖表情立绘 [1] |
| 🌐 **中英双语** | `/language` 切换 |
| 🗣️ **自由发言** | 兴趣驱动 + 对话线程连续，像真人一样在群里接话 |

---

## 前置条件

1. **Python 3.10+**
2. **Discord Bot Token**（[Discord Developer Portal](https://discord.com/developers/applications) 创建应用 → Bot → 获取 Token）
3. **LLM 后端**（OpenAI 兼容接口，支持 llama.cpp / vLLM / Ollama 等）
4. （可选）**PTV API Key**（墨尔本公共交通，[免费申请](https://www.ptv.vic.gov.au/footer/data-and-reporting/datasets/ptv-timetable-api/)）
5. （可选）**Tavily API Key**（联网搜索，[免费注册](https://tavily.com)，每月 1,000 次）

---

## 快速启动

### 1. 克隆仓库

```bash
git clone git@github.com:Wersd234/Amadeus.git
cd Amadeus
```

### 2. 配置 .env

```bash
cp .env.example .env
```

用文本编辑器打开 `.env`，至少填这三项：

```env
# ===== 必填 =====
DISCORD_BOT_TOKEN=你的Discord机器人Token

# LLM 后端地址（OpenAI 兼容格式）
LLM_BASE_URL=http://192.168.0.58:1234/v1
LLM_API_KEY=123

# ===== 可选 =====
# 模型名 — 留空则后端自动选择。填 qwen 开头的名字会自动开启视觉识别。
LLM_MODEL_NAME=

# 联网搜索（不填则 /search 不可用）
TAVILY_API_KEY=你的Tavily密钥

# 墨尔本公交（不填则 /ptv 系列不可用）
PTV_DEVELOPER_ID=你的PTV开发者ID
PTV_API_KEY=你的PTV密钥
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 启动

```bash
python3 src/main.py
```

看到以下日志即成功：

```
✅ Amadeus 'xxx' Online | WL: 1.xxxxxx% (beta) SG=False Conv=False
```

---

## 🐳 Docker 部署

```bash
# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

`.env` 中的 `LLM_BASE_URL` 需改为 `http://host.docker.internal:1234/v1`（容器访问宿主机）。

---

## 🖼️ 立绘配置

1. 把图片按原目录名放入 `resource/kurisu/`，例如：

```
resource/
├── 分类结果清单.csv
└── kurisu/
    └── Makise Kurisu(风衣)/
        ├── crs_asa01a.png
        ├── crs_asa03a.png
        └── ...
```

2. 启动 bot，它会自动解析 CSV 生成 `data/image_index.json` 缓存。

3. 在 Discord 里用 `/art_channel #频道名` 指定立绘频道。该频道的 bot 回复会自动附带对应表情的立绘。

可用表情：平静自然、苦恼不快、愤怒烦躁等 [1]。

---

## 📋 全部指令

| 指令 | 说明 |
|---|---|
| 💬 `@Amadeus` | 聊天（必定回复） |
| 🌏 `/worldline` | 查看当前世界线变动率 |
| 📜 `/worldline_history` | 查看世界线变动历史 |
| 📨 `/dmail [内容]` | 发送 D-Mail |
| 🚆 `/ptv_next [站名]` | 查下一班车 |
| ⚠️ `/ptv_status [线路]` | 查线路中断 |
| ⏰ `/ptv_subscribe` | 订阅通勤提醒 |
| 📋 `/ptv_subscriptions` | 查看订阅列表 |
| ❌ `/ptv_unsubscribe [ID]` | 取消订阅 |
| 🔬 `/lab_fortune` | 每日运势抽签 |
| 💝 `/affinity` | 查看熟络程度 |
| 🔍 `/search [关键词]` | 联网搜索 |
| 🖼️ `/art_channel [#频道]` | [管理员] 设置立绘频道 |
| 📡 `/set_broadcast [#频道]` | [管理员] 设置世界线播报频道 |
| 🌐 `/language` | 切换语言 |

---

## 🔧 配置参数（.env 高级选项）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `LLM_CTX_SIZE` | 32768 | 上下文窗口大小 |
| `WORLDLINE_REFRESH_HOURS` | 6 | 世界线自动刷新间隔（小时） |
| `DMAIL_SHIFT_PROB` | 0.2 | D-Mail 触发世界线变动概率 |
| `LLM_VISION_ENABLED` | 自动检测 | 设为 true/false 强制开关视觉 |

---

## 项目结构

```
Amadeus/
├── src/
│   ├── main.py          # Discord 客户端入口
│   ├── config.py        # 全部配置常量
│   ├── llm_api.py       # LLM 接口（OpenAI 兼容）
│   ├── worldline.py     # 世界线状态机 + D-Mail + 收束
│   ├── ptv_api.py       # PTV 交通 API（HMAC-SHA1 签名）
│   ├── image_index.py   # 立绘索引（CSV → JSON）
│   ├── commands.py      # 15 条斜杠指令
│   ├── prompts.py       # 系统提示词装配器
│   └── utils.py         # 数据持久化 + 好感度 + 订阅
├── properties/system_prompts/
│   ├── base_zh.txt      # 紅莉栖中文人设
│   ├── base_en.txt      # Kurisu English persona
│   ├── alpha_zh.txt     # α 世界线覆写（SERN 监控）
│   ├── alpha_en.txt
│   ├── special_zh.txt   # 特殊世界线覆写（性格反转）
│   └── special_en.txt
├── resource/
│   ├── 分类结果清单.csv  # 立绘元数据
│   └── kurisu/          # 立绘 PNG 文件
├── data/                # 运行时数据（卷挂载目标）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```