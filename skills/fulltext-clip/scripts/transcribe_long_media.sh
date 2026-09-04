#!/usr/bin/env bash

set -euo pipefail

usage() {
  printf '%s\n' \
    "Usage: transcribe_long_media.sh --input FILE --job-dir DIR [options]" \
    "" \
    "Options:" \
    "  --model FILE          whisper.cpp model (default: ~/whisper-models/ggml-large-v3-turbo.bin)" \
    "  --whisper-cli FILE    whisper-cli executable (default: resolve from command path)" \
    "  --language LANG       whisper language code (default: zh)" \
    "  --prompt TEXT         vocabulary/context prompt" \
    "  --segment-minutes N   chunk length in minutes (default: 20)" \
    "  --jobs N              concurrent whisper jobs: 1 or 2 (default: 2)" \
    "  --help                show this message"
}

fail() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

input_path=""
job_dir=""
model_path="${HOME}/whisper-models/ggml-large-v3-turbo.bin"
whisper_command="whisper-cli"
language="zh"
prompt=""
segment_minutes=20
jobs=2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)
      [[ $# -ge 2 ]] || fail "--input requires a value"
      input_path="$2"
      shift 2
      ;;
    --job-dir)
      [[ $# -ge 2 ]] || fail "--job-dir requires a value"
      job_dir="$2"
      shift 2
      ;;
    --model)
      [[ $# -ge 2 ]] || fail "--model requires a value"
      model_path="$2"
      shift 2
      ;;
    --whisper-cli)
      [[ $# -ge 2 ]] || fail "--whisper-cli requires a value"
      whisper_command="$2"
      shift 2
      ;;
    --language)
      [[ $# -ge 2 ]] || fail "--language requires a value"
      language="$2"
      shift 2
      ;;
    --prompt)
      [[ $# -ge 2 ]] || fail "--prompt requires a value"
      prompt="$2"
      shift 2
      ;;
    --segment-minutes)
      [[ $# -ge 2 ]] || fail "--segment-minutes requires a value"
      segment_minutes="$2"
      shift 2
      ;;
    --jobs)
      [[ $# -ge 2 ]] || fail "--jobs requires a value"
      jobs="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n "$input_path" ]] || fail "--input is required"
[[ -f "$input_path" ]] || fail "input file not found: $input_path"
[[ -n "$job_dir" ]] || fail "--job-dir is required"
[[ -f "$model_path" ]] || fail "model file not found: $model_path"
[[ "$segment_minutes" =~ ^[1-9][0-9]*$ ]] || fail "--segment-minutes must be a positive integer"
[[ "$jobs" == "1" || "$jobs" == "2" ]] || fail "--jobs must be 1 or 2"

for dependency in ffmpeg ffprobe python3; do
  command -v "$dependency" >/dev/null 2>&1 || fail "missing dependency: $dependency"
done
if [[ "$whisper_command" == */* ]]; then
  [[ -x "$whisper_command" ]] || fail "whisper-cli is not executable: $whisper_command"
else
  command -v "$whisper_command" >/dev/null 2>&1 || fail "missing dependency: $whisper_command"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
merge_script="$script_dir/merge_srt.py"
[[ -f "$merge_script" ]] || fail "merge helper not found: $merge_script"

chunks_dir="$job_dir/chunks"
logs_dir="$job_dir/logs"
status_file="$job_dir/status.json"
meta_file="$job_dir/job.meta"
failed_file="$job_dir/failed_chunks.txt"
mkdir -p "$chunks_dir" "$logs_dir"

segment_seconds=$((segment_minutes * 60))
current_meta=$(printf 'input=%s\nmodel=%s\nwhisper_cli=%s\nlanguage=%s\nprompt=%s\nsegment_seconds=%s\n' \
  "$input_path" "$model_path" "$whisper_command" "$language" "$prompt" "$segment_seconds")

if [[ -f "$meta_file" ]]; then
  existing_meta="$(<"$meta_file")"
  [[ "$existing_meta" == "$current_meta" ]] || fail \
    "job metadata differs; choose a new --job-dir instead of mixing runs"
else
  printf '%s' "$current_meta" > "$meta_file"
fi

write_status() {
  local state="$1"
  local total=0
  local complete=0
  local failed_count=0
  local wav_path base

  for wav_path in "$chunks_dir"/chunk_*.wav; do
    [[ -e "$wav_path" ]] || continue
    total=$((total + 1))
    base="${wav_path%.wav}"
    if [[ -s "$base.txt" && -s "$base.srt" ]]; then
      complete=$((complete + 1))
    fi
  done
  if [[ -f "$failed_file" ]]; then
    while IFS= read -r base; do
      [[ -n "$base" ]] && failed_count=$((failed_count + 1))
    done < "$failed_file"
  fi
  printf '{"status":"%s","chunks_total":%d,"chunks_complete":%d,"chunks_failed":%d}\n' \
    "$state" "$total" "$complete" "$failed_count" > "$status_file"
}

if ! compgen -G "$chunks_dir/chunk_*.wav" >/dev/null; then
  write_status "extracting"
  if ! ffmpeg -hide_banner -loglevel error -y \
    -i "$input_path" -map 0:a:0 -ar 16000 -ac 1 -c:a pcm_s16le \
    -f segment -segment_time "$segment_seconds" -reset_timestamps 1 \
    "$chunks_dir/chunk_%03d.wav"; then
    write_status "failed"
    fail "ffmpeg could not extract the first audio stream"
  fi
fi

compgen -G "$chunks_dir/chunk_*.wav" >/dev/null || fail "no audio chunks were created"
: > "$failed_file"
write_status "transcribing"

run_chunk() {
  local wav_path="$1"
  local output_base="${wav_path%.wav}"
  local chunk_name
  local -a whisper_args
  chunk_name="$(basename "$output_base")"
  whisper_args=(
    "$whisper_command"
    -m "$model_path"
    -f "$wav_path"
    -l "$language"
    -bs 5
    -otxt
    -osrt
    -of "$output_base"
  )
  if [[ -n "$prompt" ]]; then
    whisper_args+=(--prompt "$prompt")
  fi
  if ! "${whisper_args[@]}" > "$logs_dir/$chunk_name.log" 2>&1; then
    return 1
  fi
  [[ -s "$output_base.txt" && -s "$output_base.srt" ]]
}

declare -a batch_pids=()
declare -a batch_names=()
declare -a failed_names=()

flush_batch() {
  local index
  for ((index = 0; index < ${#batch_pids[@]}; index++)); do
    if ! wait "${batch_pids[$index]}"; then
      failed_names+=("${batch_names[$index]}")
    fi
  done
  batch_pids=()
  batch_names=()
  write_status "transcribing"
}

for wav_path in "$chunks_dir"/chunk_*.wav; do
  output_base="${wav_path%.wav}"
  if [[ -s "$output_base.txt" && -s "$output_base.srt" ]]; then
    continue
  fi
  run_chunk "$wav_path" &
  batch_pids+=("$!")
  batch_names+=("$(basename "$output_base")")
  if [[ ${#batch_pids[@]} -ge $jobs ]]; then
    flush_batch
  fi
done

if [[ ${#batch_pids[@]} -gt 0 ]]; then
  flush_batch
fi

if [[ ${#failed_names[@]} -gt 0 ]]; then
  printf '%s\n' "${failed_names[@]}" > "$failed_file"
  write_status "retrying"
  retry_names=("${failed_names[@]}")
  failed_names=()
  for chunk_name in "${retry_names[@]}"; do
    wav_path="$chunks_dir/$chunk_name.wav"
    if ! run_chunk "$wav_path"; then
      failed_names+=("$chunk_name")
    fi
    write_status "retrying"
  done
fi

if [[ ${#failed_names[@]} -gt 0 ]]; then
  printf '%s\n' "${failed_names[@]}" > "$failed_file"
  write_status "failed"
  fail "one or more chunks failed after one retry; inspect $failed_file and per-chunk logs"
fi

: > "$job_dir/transcript.txt"
for wav_path in "$chunks_dir"/chunk_*.wav; do
  output_base="${wav_path%.wav}"
  [[ -s "$output_base.txt" && -s "$output_base.srt" ]] || fail \
    "missing output for $(basename "$output_base")"
  printf '## %s\n\n' "$(basename "$output_base")" >> "$job_dir/transcript.txt"
  cat "$output_base.txt" >> "$job_dir/transcript.txt"
  printf '\n\n' >> "$job_dir/transcript.txt"
done

if ! python3 "$merge_script" \
  --chunks-dir "$chunks_dir" \
  --output "$job_dir/transcript.srt"; then
  write_status "failed"
  fail "could not merge SRT files"
fi

if [[ -f "$failed_file" ]]; then
  : > "$failed_file"
fi
write_status "complete"
printf 'complete: %s and %s\n' "$job_dir/transcript.txt" "$job_dir/transcript.srt"
