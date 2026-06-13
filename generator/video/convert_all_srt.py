import os
import json
import re
from pathlib import Path
from generator.text.content_gate import find_caption_chunk_span
from shared.utils.config import FINAL_METADATA_FILE, MARKS_DIR, SUBTITLES_DIR

def ms_to_srt_time(ms):
    seconds, milliseconds = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

def convert_single_mark_file(json_path: Path, srt_path: Path, metadata: dict | None = None):
    entries = _load_mark_entries(json_path)
    if not entries:
        print(f"❌ No valid entries found in {json_path.name}")
        return "failed"

    if metadata and metadata.get("caption_chunks"):
        try:
            captions = align_caption_chunks_to_marks(metadata.get("caption_chunks") or [], entries)
            timing_warnings = _caption_timing_warnings(captions)
            metadata["caption_timing_warnings"] = timing_warnings
            metadata["caption_timing_status"] = "warning" if timing_warnings else "ok"
            if timing_warnings and _is_production_env() and not _allow_caption_timing_warning():
                metadata["caption_alignment_status"] = "failed"
                metadata["caption_timing_status"] = "failed"
                metadata["caption_chunk_count"] = 0
                srt_path.unlink(missing_ok=True)
                print(f"🚫 Caption timing failed in production for {json_path.name}: {timing_warnings}")
                return "failed"
            _write_srt_entries(captions, srt_path)
            metadata["caption_alignment_status"] = "aligned"
            metadata["caption_chunk_count"] = len(captions)
            metadata["caption_alignment_warnings"] = []
            return "aligned"
        except Exception as exc:
            warnings = [f"caption_chunk_alignment_failed:{exc}"]
            metadata["caption_alignment_warnings"] = warnings
            if _is_production_env() and not _allow_caption_alignment_fallback():
                metadata["caption_alignment_status"] = "failed"
                metadata["caption_chunk_count"] = 0
                srt_path.unlink(missing_ok=True)
                print(f"🚫 Caption alignment failed in production for {json_path.name}: {exc}")
                return "failed"
            _write_word_grouping_srt(entries, srt_path)
            metadata["caption_alignment_status"] = "fallback_word_grouping"
            metadata["caption_chunk_count"] = 0
            return "fallback_word_grouping"

    _write_word_grouping_srt(entries, srt_path)
    return "fallback_word_grouping"


def _load_mark_entries(json_path: Path) -> list[dict]:
    entries = []
    with open(json_path, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
        # 배열로 시작하는 경우
        if first_line.startswith('['):
            f.seek(0)
            try:
                entries = json.load(f)
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON decode error at {json_path.name} → {e}")
                return
        else:
            # LDJSON 처리
            if first_line:
                try:
                    entries.append(json.loads(first_line))
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON decode error at {json_path.name}:1 → {e}")
            for line_num, line in enumerate(f, start=2):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON decode error at {json_path.name}:{line_num} → {e}")
                    continue
    return entries


def _write_word_grouping_srt(entries: list[dict], srt_path: Path) -> None:
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(srt_path, 'w', encoding='utf-8') as out:
        for i in range(len(entries) - 1):
            start = ms_to_srt_time(entries[i]['time'])
            end = ms_to_srt_time(entries[i+1]['time'])
            word = entries[i]['value']

            out.write(f"{i + 1}\n")
            out.write(f"{start} --> {end}\n")
            out.write(f"{word}\n\n")

        if entries:
            last = entries[-1]
            out.write(f"{len(entries)}\n")
            out.write(f"{ms_to_srt_time(last['time'])} --> {ms_to_srt_time(last['time'] + 500)}\n")
            out.write(f"{last['value']}\n")


def align_caption_chunks_to_marks(chunks: list[str], marks: list[dict]) -> list[tuple[int, int, str]]:
    word_marks = [mark for mark in marks if mark.get("type") in (None, "word") and str(mark.get("value") or "").strip()]
    normalized_marks = [
        mark_tokens[0]
        for mark in word_marks
        if (mark_tokens := _normalize_words(str(mark.get("value") or "")))
    ]
    captions: list[tuple[int, int, str]] = []
    cursor = 0
    max_gap = _caption_chunk_max_token_gap()
    for chunk in chunks:
        clean_chunk = re.sub(r"\s+", " ", str(chunk or "")).strip()
        tokens = _normalize_words(clean_chunk)
        if not tokens:
            continue
        start_index, end_index, reason = find_caption_chunk_span(tokens, normalized_marks, cursor, max_gap)
        if start_index < 0:
            raise ValueError(reason)
        cursor = end_index + 1
        start = int(word_marks[start_index].get("time") or 0)
        if end_index + 1 < len(word_marks):
            end = int(word_marks[end_index + 1].get("time") or start + 500)
        else:
            end = int(word_marks[end_index].get("time") or start) + 500
        captions.append((start, max(start + 200, end), clean_chunk))
    if not captions:
        raise ValueError("no_caption_chunks_aligned")
    return captions


def _normalize_words(text: str) -> list[str]:
    return [match.group(0).lower().strip("'") for match in re.finditer(r"[A-Za-z0-9']+", str(text or ""))]


def _caption_timing_warnings(captions: list[tuple[int, int, str]]) -> list[str]:
    warnings: list[str] = []
    min_ms = int(_float_env("CAPTION_MIN_DURATION_SECONDS", 0.35) * 1000)
    max_ms = int(_float_env("CAPTION_MAX_DURATION_SECONDS", 2.2) * 1000)
    final_max_ms = int(_float_env("CAPTION_FINAL_QUESTION_MAX_DURATION_SECONDS", 3.0) * 1000)
    for index, (start, end, text) in enumerate(captions, start=1):
        duration = int(end) - int(start)
        max_allowed = final_max_ms if str(text or "").rstrip().endswith("?") else max_ms
        if duration < min_ms:
            warnings.append(f"caption_{index}_too_short:{duration / 1000:.2f}s")
        if duration > max_allowed:
            warnings.append(f"caption_{index}_too_long:{duration / 1000:.2f}s")
    return warnings


def _write_srt_entries(captions: list[tuple[int, int, str]], srt_path: Path) -> None:
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as out:
        for index, (start, end, text) in enumerate(captions, start=1):
            out.write(f"{index}\n")
            out.write(f"{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}\n")
            out.write(f"{text}\n\n")


# def convert_all_marks_to_srt():
#     print("🔍 현재 경로:", MARKS_DIR.resolve())
    
#     # 모든 JSON 파일 중 '_marks.json'으로 끝나는 것만 추출
#     files = [f for f in MARKS_DIR.glob("*.json") if f.name.endswith("_marks.json")]

#     if not files:
#         print("❌ No marks files found in", MARKS_DIR)
#         return

#     for file in files:
#         print("✅ 발견:", file.name)
#         srt_name = file.stem.replace("_marks", "") + ".srt"
#         srt_path = SUBTITLE_DIR / srt_name
#         print(f"🎯 Converting: {file.name} -> {srt_name}")
#         convert_single_mark_file(file, srt_path)

#     print("✅ All files converted to SRT successfully!")


def convert_all_marks_to_srt():
    files = list(MARKS_DIR.glob("*_marks.json"))
    if not files:
        print("❌ No marks files found in", MARKS_DIR)
        return

    metadata_by_id = _load_metadata_by_id()
    for file in files:
        srt_name = file.stem.replace("_marks", "") + ".srt"
        srt_path = SUBTITLES_DIR / srt_name
        content_id = file.stem.replace("_marks", "")
        print(f"🎯 Converting: {file.name} -> {srt_name}")
        convert_single_mark_file(file, srt_path, metadata_by_id.get(content_id))

    _save_metadata(metadata_by_id)
    print("✅ All files converted to SRT successfully!")


def _load_metadata_by_id() -> dict[str, dict]:
    if not FINAL_METADATA_FILE.exists():
        return {}
    try:
        with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            return {}
        return {str(item.get("id")): item for item in items if isinstance(item, dict) and item.get("id")}
    except Exception as exc:
        print(f"⚠️ final metadata load failed for captions: {exc}")
        return {}


def _save_metadata(metadata_by_id: dict[str, dict]) -> None:
    if not metadata_by_id or not FINAL_METADATA_FILE.exists():
        return
    try:
        with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            return
        merged = []
        for item in items:
            content_id = str(item.get("id") or "")
            merged.append(metadata_by_id.get(content_id, item))
        with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"⚠️ final metadata caption status save failed: {exc}")


def _is_production_env() -> bool:
    return any(os.getenv(name, "").strip().lower() == "production" for name in ("APP_ENV", "YT_ENV"))


def _allow_caption_alignment_fallback() -> bool:
    return os.getenv("ALLOW_CAPTION_ALIGNMENT_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}


def _allow_caption_timing_warning() -> bool:
    return os.getenv("ALLOW_CAPTION_TIMING_WARNING", "").strip().lower() in {"1", "true", "yes", "on"}


def _caption_chunk_max_token_gap() -> int:
    try:
        return max(0, int(os.getenv("CAPTION_CHUNK_MAX_TOKEN_GAP", "2")))
    except ValueError:
        return 2


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default

if __name__ == "__main__":
    convert_all_marks_to_srt()
