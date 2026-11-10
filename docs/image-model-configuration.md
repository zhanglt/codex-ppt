# Image Model Configuration

Use this reference only when the local API/CLI fallback is needed and the runtime config is missing or must be changed.

Do not manually parse `.env`. The fallback CLI loads the shared config automatically. Run the fallback command first, then use this document only if the CLI reports missing or invalid configuration.

Ask the user to configure or update settings only when:

- The fallback CLI reports missing `OPENAI_API_KEY`.
- The user explicitly wants to change API key, base URL, or model.
- A real API call fails with authentication, permission, base URL, or model-not-found errors.

## When Configuration Is Needed

Configure image API access only for API/CLI fallback image generation.

Typical cases:

- Codex is using a third-party API or OpenAI-compatible proxy for image generation.
- The skill is being used from Claude Code, OpenClaw, Hermes Agent, or another agent without Codex's built-in image tool.

If Codex is being used through a GPT subscription and the built-in image tool is available, do not ask the user to configure `gpt-image-2.5-flare`.

## Required And Optional Values

### 按服务商分组选择

共享文件仍为 `${CODEX_PPT_HOME:-~/.codex-ppt-skill}/.env`（不是 `.nev`）。支持以下扩展格式：

```dotenv
IMAGE_PROVIDER=nebula

[nebula]
NEBULA_API_KEY=your-nebula-api-key
NEBULA_BASE_URL=https://img-api.apinebula.ai/v1
NEBULA_PPT_IMAGE_MODEL=gpt-image-2-4k
NEBULA_SIZE=3840x2160
NEBULA_QUALITY=high

[openai]
OPENAI_API_KEY=your-openai-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
CODEX_PPT_IMAGE_MODEL=gpt-image-2.5-flare
```

- `IMAGE_PROVIDER` 必须放在第一个分组之前，支持 `nebula`、`openai`、`atlascloud`；后者使用 `[atlascloud]` 分组。
- 只加载选中的分组，不读取其他分组或顶部旧配置的密钥。未知服务商、缺失分组、分组存在但未指定选择项均报错。
- `[nebula]` 使用 `NEBULA_API_KEY`、`NEBULA_BASE_URL`、`NEBULA_PPT_IMAGE_MODEL`；仍兼容分组内旧的通用字段，同时存在时以 `NEBULA_*` 字段为准。`config` 更新此分组时统一写入新字段。进程中的通用环境变量仍按原优先级覆盖文件值。
- `NEBULA_SIZE`、`NEBULA_QUALITY` 分别设置 Nebula 的默认尺寸和质量，适用于生成、编辑和批量生成。优先级为命令行 `--size` / `--quality` > 同名进程环境变量 > 分组配置 > 内置默认值。省略时使用 `3840x2160`、`high`；非法值仍会被参数校验拒绝。
- 分组内省略 base URL 时使用对应服务商的默认地址；Nebula 未配置模型时默认 `gpt-image-2-4k`，其他服务默认 `gpt-image-2.5-flare`。AtlasCloud 应填写其实际支持的模型。
- 优先级为：命令行 `--base-url` / `--model` > 进程环境变量 > 选中分组。进程变量 `IMAGE_PROVIDER` 可临时切换分组。服务商与最终 base URL 的适配器必须一致，否则报错，避免混用密钥和接口。
- 不含分组且未设置 `IMAGE_PROVIDER` 的旧配置继续按 base URL 自动选择适配器。
- 这是工具包自带解析器支持的格式；不要直接作为 shell 脚本执行，也不要用普通 dotenv 加载器解析分组。

切换服务商，保留其他分组：

```powershell
python scripts/codex_ppt_runtime.py config --provider openai
```

更新选中分组的模型（也可提供 `--api-key`、`--base-url`）：

```powershell
python scripts/codex_ppt_runtime.py config --provider nebula --model gpt-image-2-4k
```

`config` 无 `--provider` 时更新文件中当前选中的分组；原无分组配置仍更新顶层字段。写回保留其他分组的字段，但不保留原有注释和排版。配置文件使用 UTF-8 无 BOM；本次切换不提交图片请求。

- `OPENAI_API_KEY` is required for real API/CLI fallback calls.
- `OPENAI_BASE_URL` is optional. When it is unset, the CLI uses the official OpenAI API. When it is set, the CLI uses the configured third-party provider base URL.
- `CODEX_PPT_IMAGE_MODEL` is optional. The default is `gpt-image-2.5-flare`. Use `gpt-image-2.5-sunburst` to select Sunburst, or a model name supported by the provider.

Configure provided API settings with `scripts/codex_ppt_runtime.py config --api-key`. The config command writes `~/.codex-ppt-skill/.env`.

## Official OpenAI Example

```bash
python3 {skill_root}/scripts/codex_ppt_runtime.py config \
  --api-key "your-api-key" \
  --model gpt-image-2.5-flare
```

## OpenAI-Compatible Provider Example

Use this shape for providers that implement the OpenAI Images API paths used by the fallback CLI.

```bash
python3 {skill_root}/scripts/codex_ppt_runtime.py config \
  --api-key "your-provider-api-key" \
  --base-url "https://xxxx.example.com/v1" \
  --model gpt-image-2.5-flare
```

This produces the same effective runtime config as:

```env
OPENAI_API_KEY=your-provider-api-key
OPENAI_BASE_URL=https://xxxx.example.com/v1
CODEX_PPT_IMAGE_MODEL=gpt-image-2.5-flare
```

For OpenAI-compatible providers, `OPENAI_BASE_URL` should normally end at the provider's `/v1` root. Do not set it to `/images/generations`, `/images/edits`, or another terminal endpoint. The fallback CLI appends the image-generation or image-edit path through the OpenAI SDK.

Use the provider's model name only when the provider documents a custom name. Otherwise prefer `gpt-image-2.5-flare`.

## Nebula image2-4k

Nebula `image2-4k` 的配置与调用示例见 [Nebula 图片 API](nebula-image-api.md)。其 base URL 为 `https://img-api.apinebula.ai/v1`，模型须为 `gpt-image-2-4k`，令牌须属于 `image2-4k` 分组；该适配器使用专用 HTTP 请求，不依赖通用 SDK 的默认行为。

## AtlasCloud Example

For AtlasCloud, set `--model` to the base model name. The CLI chooses the matching generation or editing model route internally. This example retains the known `gpt-image-2` route; check provider documentation before selecting a 2.5 model.

```bash
python3 {skill_root}/scripts/codex_ppt_runtime.py config \
  --api-key "your-atlascloud-api-key" \
  --base-url "https://api.atlascloud.ai/api/v1/model" \
  --model openai/gpt-image-2
```

## Runtime Config

The config is written to:

```text
~/.codex-ppt-skill/.env
```

The file is created with mode `0600`. It is shared by Codex, Claude Code, OpenClaw, Hermes Agent, and other local agents.

Process environment variables override `.env` values. A command-line `--model` overrides `CODEX_PPT_IMAGE_MODEL` for that single command.

命令行 `--base-url` 仅为本次调用覆盖 `OPENAI_BASE_URL`，不会改写共享配置。选中 Nebula 且未配置模型时默认使用 `gpt-image-2-4k`；已有的显式模型配置不会被覆盖。
