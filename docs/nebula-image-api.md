# Nebula 图片 API

此适配器用于 APINebula `image2-4k` 分组，支持文本生图和一张或多张参考图编辑。
接口依据：[官方 image2-4k 文档](https://docs.apinebula.ai/docs/advanced/image/image2-4k)，核对日期：2026-11-07。

## 配置与接口

继续使用共享运行目录 `${CODEX_PPT_HOME:-~/.codex-ppt-skill}/.env`，无需另设密钥文件：

```dotenv
OPENAI_API_KEY=your-nebula-api-key
OPENAI_BASE_URL=https://img-api.apinebula.ai/v1
CODEX_PPT_IMAGE_MODEL=gpt-image-2-4k
```

令牌须选择 `image2-4k` 分组。不要将实际密钥写入项目文件或提示词。
进程环境变量优先于 `.env`；`--base-url`、`--model` 优先于对应配置，仅影响本次命令。
既有共享配置不会自动改写；需要永久切换时，使用现有 `codex_ppt_runtime.py config` 命令更新对应字段。

仅当 hostname 精确匹配 `img-api.apinebula.ai` 时启用适配器。base URL 使用 HTTPS 并以 `/v1` 结尾，不填写具体接口路径。

- 生图：`POST /v1/images/generations`，JSON 请求。
- 编辑：`POST /v1/images/edits`，multipart 请求，多张参考图重复 `image` 字段。
- 模型：`gpt-image-2-4k`；尺寸：仅 `3840x2160`；默认质量：`high`。
- 未显式配置模型时，Nebula 路由使用该模型；已有模型配置需自行确认，错误型号会被拒绝。

## 生图示例

在技能根目录运行，使用已安装依赖的 Python；Windows 共享解释器通常为 `~/.codex-ppt-skill/.venv/Scripts/python.exe`。

```powershell
python -X utf8 scripts/image_gen.py generate --base-url https://img-api.apinebula.ai/v1 --model gpt-image-2-4k --prompt "一页蓝白配色的水利信息化汇报封面，16:9 横屏" --out output/cover.png
```

可先附加 `--dry-run` 检查请求，不提交生图。选中 Nebula 后，未填写的 `--size`、`--quality` 自动使用 `3840x2160`、`high`。已有共享密钥仍须与该服务兼容。

## 编辑与多参考图

```powershell
python -X utf8 scripts/image_gen.py edit --base-url https://img-api.apinebula.ai/v1 --model gpt-image-2-4k --image input/slide.png --image input/logo.png --input-fidelity high --prompt "保留原有布局和文字，将右上角标志替换为第二张参考图" --out output/slide-edited.png
```

重复 `--image` 即可附加多张 PNG、JPEG 或 WebP；单个输入文件限制为 50 MB。编辑结果须检查文字、主体一致性及实际尺寸后再替换最终幻灯片。

## 参数及输出

- `--n` 支持 1–10，作为一次请求传入；逐页工作流建议每个任务使用 `--n 1`。
- `--quality` 接受 `low`、`medium`、`high`、`auto`；获取 4K 使用 `high`。
- `--response-format url` 为默认值，也可使用 `b64_json`；按指定格式优先取值，缺失时读取另一种格式。
- 官方仅声明服务端支持 JPEG，因此 API 请求固定 `output_format=jpeg`。本地默认保存为 PNG；`--output-format png/jpeg/webp` 决定本地格式，必要时通过 Pillow 转换。dry-run 同时显示接口格式和 `local_output_format`。
- `--output-compression 0–100` 作用于 API 生成的 JPEG；之后本地转码不会恢复已损失的信息。
- 支持 `--background opaque`、`--moderation auto/low`；不支持透明背景、`--mask` 和 `--input-fidelity low`。未声明的能力不作兼容承诺。
- 收到图片后检查可解码性和实际像素；尺寸不是 `3840x2160` 时发出警告，不能将请求尺寸当成实测结果。

## 超时、批量与验证

API 请求超时设为 1800 秒，CDN 下载为 120 秒。下载不携带 API Authorization；下载失败不会重新生图。批量模式同样适用，Nebula 不使用 `--max-attempts` 自动重试提交，防止响应丢失或 CDN 失败后重复计费。失败后先检查服务端任务和链接有效期。

每次收到有效 API 响应后，先将响应保存在共享运行目录的 `cache/nebula-responses/`，再下载图片；日志会显示缓存路径，不输出密钥。若 CDN 下载失败但同一响应包含 Base64，会使用该 Base64。缓存可能包含完整图片和临时签名链接，应作为本地私有文件保管。

仅重试取图，既不需要提示词也不提交生图请求：

```powershell
python -X utf8 scripts/image_gen.py resume --response-file "C:/Users/Administrator/.codex-ppt-skill/cache/nebula-responses/<id>.json" --out output/recovered.png
```

URL 过期后，仅含 URL 的缓存无法还原图片。旧版未保存响应的失败请求也无法通过本命令恢复；不要将重新生图称为重试下载。

`doctor --check-api` 在 Nebula 路由下不探测 `/models`，也不提交计费图片请求；其成功不能证明真实生图或编辑成功。

离线回归测试：

```powershell
python -X utf8 -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

测试覆盖 JSON 生图、multipart 多图编辑、URL/Base64 响应、格式转换、错误处理、禁止自动重提、配置优先级及其他服务路由。真实接口仍取决于令牌分组、余额和服务端可用性。
