# Repository Guidelines

## 项目结构与模块组织

本项目是生成图片型 PPT 的 Codex 技能包。`SKILL.md` 是流程入口；`docs/` 存放审批、后端、素材、组装与交付规范；`prompts/` 存放单页任务模板；`references/` 存放内置视觉风格。Python 源码位于 `scripts/`，图片服务适配器位于 `scripts/image_providers/`。依赖见 `requirements.txt`。当前没有独立的 `tests/` 目录；生成的图片、提示词和 PPT 应放入单独的项目输出目录。

## 开发与验证命令

在技能根目录运行，使用已安装依赖的 Python 环境：

```powershell
python scripts/codex_ppt_runtime.py bootstrap
python scripts/codex_ppt_runtime.py doctor
python -m compileall -q scripts
python scripts/image_gen.py generate --prompt "测试幻灯片" --dry-run
python scripts/slide_job_status.py <deck_dir>
```

`bootstrap` 创建共享虚拟环境并安装依赖；`doctor` 检查运行环境；`compileall` 检查语法；`--dry-run` 验证生图请求流程；状态命令检查逐页任务。实际组装使用 `python scripts/assemble_ppt.py <base_dir> <deck_name>.pptx`，须先完成流程审批与结果记录。项目没有单独的构建系统。

## 编码风格与命名

Python 使用四空格缩进，函数、变量和模块采用 `snake_case`，类采用 `PascalCase`；修改时保持周边代码风格和类型标注。当前未配置统一 formatter 或 linter。逐页文件使用 `slide_01`、`slide_02` 等补零编号。说明性内容以中文为主，技术标识保留英文。

所有 `.md`、`.json`、`.yaml` 文件均按 UTF-8 无 BOM 读写。禁止使用 `>`、`>>` 或默认编码的 `Out-File` 写入；PowerShell 使用 `Set-Content -Encoding utf8`，并确认所用版本不会写入 BOM。

## 测试与验收

当前没有已配置的自动测试框架或覆盖率门槛。代码变更至少执行语法检查及相关 CLI 验证。图片适配变更应模拟成功响应、错误响应和图片下载；新增测试建议命名为 `tests/test_<module>.py`，并记录运行方式。涉及最终 PPT 时逐页检查文字、裁切、素材与风格，并核对任务状态。静态检查或 dry-run 通过不能替代真实 API 验证及 PowerPoint 打开检查。

## 提交与 Pull Request

当前目录不含 Git 元数据，无法核实既有提交约定。建议使用简短、明确的提交说明，例如 `fix: 修复图片响应解析`。PR 应说明问题、行为变化、影响文件和验证结果；关联相关 issue，视觉变更附前后对比图。避免混入生成产物、凭据或无关修改。

## 配置与代理执行

共享配置位于 `${CODEX_PPT_HOME:-~/.codex-ppt-skill}/.env`。不得提交或输出 API 密钥；保留现有配置加载逻辑和服务商协议。开展制作前阅读对应流程文档，遵守大纲、风格、后端及样张确认环节。样张获批后按技能要求分派逐页任务，并使用配套脚本记录结果；最终页面必须来自确认的生图后端。
