# 本地音视频转写

使用本地 `whisper.cpp` 把音频或视频转成全文。除非用户另有要求，不上传媒体、不静默改用云端服务。

## 环境自检

实际运行前检查环境，不依赖文档里的旧版本信息：

```bash
for c in whisper-cli ffmpeg ffprobe; do
  printf "%-12s %s\n" "$c" "$(command -v "$c" || echo '缺失')"
done
test -f "$WHISPER_MODEL" || echo "模型缺失"
```

缺少工具或模型时，先说明缺什么以及拟执行的安装动作；不要擅自换模型。常见安装方式：

```bash
brew install whisper-cpp ffmpeg
```

模型路径从当前项目 `.local/paths.md` 的 `$WHISPER_MODEL` 取得；没有时询问用户。

## 工作目录

从 [storage.md](storage.md) 取得 `$STAGING_DIR` 规则，为每个源文件建立独立 `$JOB_DIR`。用户源文件只读，不复制或移动到这个目录。

```bash
JOB_DIR="$STAGING_DIR/$(date +%Y-%m-%d_%H%M%S)_<内容短标识>"
mkdir -p "$JOB_DIR"
```

## 推荐流程

从 skill 根目录运行脚本。它会分块、有限并发、失败重试、合并 SRT，并允许使用相同参数和 `$JOB_DIR` 断点续跑：

```bash
bash scripts/transcribe_long_media.sh \
  --input "<输入文件>" \
  --job-dir "$JOB_DIR" \
  --language auto \
  --prompt "专有名词：<按内容填写>" \
  --jobs 2
```

输出包括 `$JOB_DIR/transcript.txt`、`transcript.srt` 和 `status.json`。

- 已知语言时用 `en` 或 `zh`，不确定才用 `auto`。
- 已知的人名、产品和术语放入 `--prompt`，长稿仍需在优化阶段统一专名。
- 超过 30 分钟的媒体必须用脚本，不临时拼接一长串命令。
- 查询进度只读 `status.json`；失败时再读 `failed_chunks.txt` 与对应日志。
- `job.meta` 显示参数变化时，新建 `$JOB_DIR`，不要混用旧分块。

需要先确认音质或语言时，可在 `$JOB_DIR` 中制作短探针；探针不能替代完整转写。

## 多文件与多人内容

- 每个文件一个 `$JOB_DIR`、一份成品；除非用户明确说明它们是同一内容的分段，否则不要合并。
- 单个文件失败时保留其目录并继续其他文件，最终分别报告。
- `whisper.cpp` 不负责说话人分离。多人内容可按上下文人工标角色；若必须自动区分说话人，应先说明需要额外工具与权限。

## 转写后

1. 在成品完成前保留原始 `transcript.txt` 与 SRT 以便回退。
2. 按 [optimize.md](optimize.md) 优化排版、统一专名；英文内容做英中段落对照。
3. 按 [source-integrity.md](source-integrity.md) 核实来源四项，核实不到就标明。
4. 按 [storage.md](storage.md) 查重并写入成品。
5. 用 `ffprobe` 取得媒体时长，根据语种和语速检查转写规模是否明显截断；无法可靠估计时报告实际字数与时长。

## 失败与访问权限

若 ffmpeg 报 `Operation not permitted`，这是宿主应用或操作系统的文件访问权限问题。先实测具体路径是否可读；可请用户把文件移动到已授权目录，或为当前宿主授予访问权限。不要声称某个目录在所有机器上都可读。

转写中断、输出为空或明显截断时，保留 `$JOB_DIR`，报告失败步骤、错误信息与已生成文件；不得用半截稿继续交付。

