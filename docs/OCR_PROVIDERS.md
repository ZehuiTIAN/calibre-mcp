# OCR 服务配置指南(2026-08)

`ocr_book` 需要一家云端 OCR 服务。本指南对比主流服务、给出官方入口和
开通步骤,以及**如何配置 API Key**。

## ⚠️ API Key 隔离(重要)

- API Key **只属于你个人**,写在你的 MCP 客户端配置里(如
  `~/.claude.json` 的 `mcpServers` 环境变量),**永远不要提交进仓库**,
  也不要发给别人。
- 每个使用者各自注册自己的 Key —— 仓库代码里没有任何 Key,你的配置
  不会影响别人,别人的也不会影响你。

## 服务对比

> 成本估算基准:一本 200 页的扫描书。价格为 2026-08 查询的公开价,
> 实际以各家控制台为准。

| 服务 | 计费 | 一本 200 页书约 | 版式结构 | 中文 | 支付/网络 | 推荐 |
| --- | --- | --- | --- | --- | --- | --- |
| **阿里云百炼 qwen-vl-ocr** | 输入 ¥0.3/M tokens<br>输出 ¥0.5/M tokens | **≈ ¥0.3** | ✅ 专为文档 OCR/版式设计 | ✅ 极佳 | 国内直连、人民币 | ⭐ **首选** |
| Azure Document Intelligence | Read $1.5/千页<br>Layout $10/千页 | ≈ ¥1.5(Layout) | ✅ Layout 版式顶级 | ✅ | 国际卡、国内访问慢 | 企业文档流 |
| 百度智能云 OCR | 标准版 0.005 元/次<br>高精度 0.03 元/次 | ≈ ¥1(标准) | ❌ 纯文字,无版式 | ✅ 好 | 国内直连、人民币 | 轻量单页票据 |
| 阿里云传统 OCR API | ≈ 0.03 元/张 | ≈ ¥6 | ❌ 纯文字,无版式 | ✅ 好 | 国内直连、人民币 | 传统接口场景 |
| Anthropic Claude(Haiku 4.5) | $1/$5 每 M tokens | ≈ ¥6–10 | ✅ 结构 + 纠错好 | ✅ | 美元、需可访问 | 追求质量与纠错 |

**结论**:中文书 OCR + 重排版场景,`qwen-vl-ocr` 价格约为 Azure 的 1/5、
Anthropic 的 1/20,国内直连人民币结算,版式输出质量高 —— 默认推荐。

## 各服务入口与开通步骤

### 阿里云百炼 qwen-vl-ocr(推荐)

1. 打开 [百炼控制台](https://bailian.console.aliyun.com/),注册/登录阿里云账号并实名
2. 右上角 **API-KEY 管理** → 创建 API Key(DashScope Key)
3. 首次调用前开通百炼按量付费(模型页有引导,几分钟完成)
4. 配置:

```json
"env": {
  "CALIBRE_OCR_PROVIDER": "dashscope",
  "CALIBRE_OCR_API_KEY": "sk-你的key",
  "CALIBRE_OCR_MODEL": "qwen-vl-ocr"
}
```

- 模型文档:[qwen-vl-ocr 官方文档](https://help.aliyun.com/zh/model-studio/qwen-vl-ocr)
- 价格文档:[百炼模型计费](https://www.alibabacloud.com/help/en/model-studio/model-pricing)
- 注意:地域选**国内(北京)**;Key 是 `sk-` 开头

### Anthropic Claude

1. 打开 [Anthropic Console](https://console.anthropic.com/) 注册,绑卡
2. **API Keys** → Create Key
3. 配置(模型默认 Haiku 4.5,可用 `CALIBRE_OCR_MODEL` 覆盖):

```json
"env": {
  "CALIBRE_OCR_PROVIDER": "anthropic",
  "CALIBRE_OCR_API_KEY": "sk-ant-你的key"
}
```

- 价格:[Anthropic 定价页](https://www.anthropic.com/pricing)

### Azure Document Intelligence

1. [Azure Portal](https://portal.azure.com/) → 创建 **Document Intelligence** 资源(免费 F0 档可用)
2. 资源页 **Keys and Endpoint** 复制 key + endpoint
3. 注意:Azure 是「Key + Endpoint」双要素。当前代码仅支持单 Key 模式,
   endpoint 可通过 `CALIBRE_OCR_BASE_URL` 传入;该 provider 尚未内置实现,
   可仿照 `ocr.AnthopicProvider` / `ocr.DashscopeProvider` 添加
4. 价格:[Azure DI 定价页](https://azure.microsoft.com/pricing/details/ai-document-intelligence/)

### 百度智能云 OCR

1. [百度智能云控制台](https://console.bce.baidu.com/) → 文字识别 → 创建应用
2. 应用详情页获取 **API Key + Secret Key**(OAuth 双要素,当前需自行实现 provider)
3. 价格:[百度 OCR 产品页](https://cloud.baidu.com/product/ocr.html)
   (新用户有免费额度:个人 500 次/月、企业 1000 次/月)

## 自定义 / 自建 Provider

Provider 接口只有两个要素,几十行即可接入:

```python
# 仿照 src/calibre_mcp/ocr.py 中的 DashscopeProvider
class MyProvider:
    name = "my-provider"          # 注册名

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        ...

    def ocr_pages(self, page_images: list[bytes], context: dict) -> str:
        ...  # 返回结构化 Markdown
        return markdown

# 注册后设 CALIBRE_OCR_PROVIDER=my-provider 即可使用
PROVIDERS["my-provider"] = MyProvider
```

接口约定:输入是 PNG 字节列表(一次调用传入约 8 张的批次),输出是
保留章节标题(# / ##)和段落结构的 Markdown。
