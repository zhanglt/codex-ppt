# TeamoRoute调用



## 一、接口地址与鉴权

TeamoRouter 使用 OpenAI Images API 兼容格式：

- 生成图片：`POST https://api.teamorouter.com/v1/images/generations`
- 编辑图片：`POST https://api.teamorouter.com/v1/images/edits`

请求头：

```http
Authorization: Bearer sk-teamo-xxxxxx
Content-Type: application/json
```

编辑接口也支持 `multipart/form-data`，用于上传图片文件。

## 二、生成图片

最小请求：

```bash
curl https://api.teamorouter.com/v1/images/generations \
  -H "Authorization: Bearer sk-teamo-xxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2.5-flare",
    "prompt": "一只在键盘上打字的橘猫，插画风格"
  }'
```

指定尺寸和质量：

```json
{
  "model": "gpt-image-2.5-flare",
  "prompt": "一只在键盘上打字的橘猫，插画风格",
  "size": "2048x2048",
  "quality": "high"
}
```

常用参数：

| 参数                 | 说明                                                         |
| :------------------- | :----------------------------------------------------------- |
| `model`              | 使用 `gpt-image-2.5-flare`。必须明确指定；省略 model 时默认使用 gpt-image-2。 |
| `prompt`             | 生图描述。                                                   |
| `size`               | `auto` 或 `宽x高`，例如 `1024x1024`、`2048x2048`、`3840x2160`。 |
| `quality`            | `low`、`medium`、`high` 或 `auto`。                          |
| `n`                  | 一次生成的图片数量，默认 1。                                 |
| `output_format`      | `png`、`jpeg` 或 `webp`。                                    |
| `output_compression` | JPEG/WebP 压缩等级，0 到 100。                               |
| `background`         | `transparent`、`opaque` 或 `auto`。`transparent` 时返回带透明通道的图片，需搭配 `output_format` 为 `png` 或 `webp`。 |
| `moderation`         | 内容审核强度，`low` 或 `auto`。                              |
| `stream`             | 设为 `true` 时以 SSE 流式返回：中间分片 `object` 为 `image.generation.chunk`，最后一个分片 `object` 为 `image.generation.result`，`data[0].b64_json` 是结果图，随后是 `data: [DONE]`。 |
| `partial_images`     | 0 到 3。当前中间分片不带图片，只有最后一个分片带结果。       |
| `user`               | 调用方自定义的最终用户标识，原样透传。                       |

OpenAI 对 `gpt-image-2.5-flare` 的尺寸约束是：最大边不超过 3840 像素；宽高均为 16 的倍数；长宽比不超过 3:1；总像素数在 655,360 到 8,294,400 之间。尺寸不是固定的 `1k`、`2k`、`4k` 字符串，而是像素尺寸。

## 三、编辑图片

```bash
curl https://api.teamorouter.com/v1/images/edits \
  -H "Authorization: Bearer sk-teamo-xxxxxx" \
  -F "model=gpt-image-2.5-flare" \
  -F "prompt=把背景替换成雪山" \
  -F "image=@input.png" \
  -F "size=1024x1024"
```

常用参数：

| 参数                 | 说明                                                         |
| :------------------- | :----------------------------------------------------------- |
| `model`              | 使用 `gpt-image-2.5-flare`。                                 |
| `image`              | 原图文件。                                                   |
| `mask`               | 遮罩图文件，透明区域为可编辑区域，尺寸与原图一致。           |
| `prompt`             | 修改指令。                                                   |
| `n`                  | 一次生成的图片数量，默认 1。                                 |
| `size`               | `auto` 或 `宽x高`。                                          |
| `quality`            | `low`、`medium`、`high` 或 `auto`。                          |
| `background`         | `transparent`、`opaque` 或 `auto`。实测编辑接口返回的图片不带透明通道。 |
| `input_fidelity`     | 对原图细节的保真程度，`high` 或 `low`。                      |
| `output_format`      | `png`、`jpeg` 或 `webp`。                                    |
| `output_compression` | JPEG/WebP 压缩等级，0 到 100。                               |
| `stream`             | 设为 `true` 时以 SSE 流式返回，事件格式与生成图片相同。      |
| `partial_images`     | 0 到 3。                                                     |
| `user`               | 调用方自定义的最终用户标识，原样透传。                       |

## 四、尺寸与计费规则

生图按张计费，每张图片的单价由它的实际分辨率决定，按最长边分为三档：

| 实际出图最长边 | 档位 |
| :------------- | :--- |
| 不超过 1024    | 1K   |
| 不超过 2048    | 2K   |
| 超过 2048      | 4K   |

判定依据是返回图片的实际宽高，不是请求参数。`size` 为 `auto`、不传或为空时，按模型实际输出的尺寸计费；不传 `size` 时模型默认输出 10