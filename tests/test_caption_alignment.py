import json

from generator.video import convert_all_srt


def _marks(words):
    return [{"time": index * 400, "type": "word", "value": word} for index, word in enumerate(words)]


def test_caption_chunks_are_converted_into_srt_entries(tmp_path, monkeypatch):
    metadata_path = tmp_path / "final_metadata.json"
    marks_dir = tmp_path / "marks"
    subtitles_dir = tmp_path / "subtitles"
    marks_dir.mkdir()
    subtitles_dir.mkdir()
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "id": "story",
                    "caption_chunks": [
                        "He parked in my driveway",
                        "Was I wrong to post the clip?",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    (marks_dir / "story_marks.json").write_text(
        json.dumps(_marks("He parked in my driveway Was I wrong to post the clip".split())),
        encoding="utf-8",
    )
    monkeypatch.setattr(convert_all_srt, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(convert_all_srt, "MARKS_DIR", marks_dir)
    monkeypatch.setattr(convert_all_srt, "SUBTITLES_DIR", subtitles_dir)

    convert_all_srt.convert_all_marks_to_srt()

    srt = (subtitles_dir / "story.srt").read_text(encoding="utf-8")
    assert "He parked in my driveway" in srt
    assert "Was I wrong to post the clip?" in srt
    assert srt.count("-->") == 2
    updated = json.loads(metadata_path.read_text(encoding="utf-8"))[0]
    assert updated["caption_alignment_status"] == "aligned"
    assert updated["caption_chunk_count"] == 2


def test_caption_alignment_fallback_is_marked(tmp_path, monkeypatch):
    metadata = {"id": "story", "caption_chunks": ["This token will not align"]}
    marks_path = tmp_path / "story_marks.json"
    srt_path = tmp_path / "story.srt"
    marks_path.write_text(json.dumps(_marks(["different", "words"])), encoding="utf-8")
    monkeypatch.setenv("ALLOW_CAPTION_ALIGNMENT_FALLBACK", "1")

    status = convert_all_srt.convert_single_mark_file(marks_path, srt_path, metadata)

    assert status == "fallback_word_grouping"
    assert metadata["caption_alignment_status"] == "fallback_word_grouping"
    assert metadata["caption_alignment_warnings"]
    assert srt_path.exists()
