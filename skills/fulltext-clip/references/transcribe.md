# 本机转写

音视频到原始文字由本机 `whisper.cpp` 完成，不上传媒体、不用云端转写额度；后续的校对、翻译和排版由当前智能体处理文字。除非用户另有要求，不静默改用云端服务。

通常不用直接调用：`prep.py init` 遇到没有现成文字的音视频会自动转写。它会用标题、主播和节目时间轴里的话题词生成转写提示词（帮 whisper 写对专名），多篇同时准备时自动排队，本机一次只转一篇。

## 环境自检

转写失败或第一次使用时检查：

```bash
for c in whisper-cli ffmpeg ffprobe; do printf "%-12s %s\n" "$c" "$(command -v "$c" || echo '缺失')"; done
test -f "$WHISPER_MODEL" || echo "模型缺失"
```

模型路径取自 `.local/paths.md` 的 `WHISPER_MODEL`，没有时询问用户。缺工具或模型时，先说明缺什么、拟执行的安装动作（如 `brew install whisper-cpp ffmpeg`），得到同意再装；不擅自换模型。

## 直接调用脚本

需要单独转写（比如准备脚本之外的文件）时，在 skill 根目录运行：

```bash
bash scripts/transcribe_long_media.sh --input "<输入文件>" --job-dir "<任务目录>/asr" \
  --language zh --prompt "专有名词：<按内容填写>" --model "$WHISPER_MODEL" --jobs 2
```

- 已知语言时写 `zh` 或 `en`，不确定才用 `auto`。
- 它会分块、有限并发、失败重试一次、合并 SRT；用相同参数和目录可以断点续跑。
- 进度只看 `asr/status.json`；失败时再看 `failed_chunks.txt` 和 `logs/` 里对应的日志。
- `job.meta` 提示参数变了时，换一个新目录，不要混用旧分块。

## 说话人

whisper.cpp 不区分说话人。本机转写的文字按 write.md 的规则处理：只给能确定的主持人提问加 🎙️，其余不标。用户明确要求区分说话人时，再评估其他办法（平台带说话人的文字稿、飞书妙记转写、本机说话人分离工具），先说明成本和把握。

## 失败

- ffmpeg 报 `Operation not permitted`：宿主应用或系统的文件访问权限问题。先实测路径是否可读，可请用户把文件移到已授权的目录，或给宿主授权。
- 转写中断、输出为空或明显截断：保留任务目录，报告失败步骤、错误信息和已生成的文件；不拿半截稿继续交付。
- 多个文件时，一个失败就保留它的目录，接着处理其他文件，最后分别报告。
