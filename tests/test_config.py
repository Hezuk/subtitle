from pathlib import Path

from config import _prepare_runtime_subdir


def test_prepare_runtime_subdir_moves_legacy_directory(tmp_path):
    base_dir = tmp_path / "project"
    runtime_dir = base_dir / "runtime"
    legacy_dir = base_dir / "uploads"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "job.mp4").write_text("video", encoding="utf-8")

    target = _prepare_runtime_subdir(base_dir, runtime_dir, "uploads")

    assert target == runtime_dir / "uploads"
    assert (target / "job.mp4").exists()
    assert not legacy_dir.exists()


def test_prepare_runtime_subdir_merges_legacy_files_into_existing_target(tmp_path):
    base_dir = tmp_path / "project"
    runtime_dir = base_dir / "runtime"
    legacy_dir = base_dir / "jobs"
    target_dir = runtime_dir / "jobs"
    legacy_dir.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    (legacy_dir / "legacy.json").write_text("{}", encoding="utf-8")
    (target_dir / "current.json").write_text("{}", encoding="utf-8")

    target = _prepare_runtime_subdir(base_dir, runtime_dir, "jobs")

    assert target == target_dir
    assert (target_dir / "legacy.json").exists()
    assert (target_dir / "current.json").exists()
    assert not legacy_dir.exists()
