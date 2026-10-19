from pas.media.ffmpeg import AudioStream, MediaInfo, VideoStream
from pas.modules.media_check.tasks import aspect_label, find_issues


def test_aspect_labels():
    assert aspect_label(1920, 1080) == "16:9"
    assert aspect_label(1080, 1920) == "9:16"
    assert aspect_label(1080, 1080) == "1:1"
    assert aspect_label(1440, 1080) == "4:3"
    assert aspect_label(1000, 300) == "3.33:1"
    assert aspect_label(0, 0) == "?"


def test_find_issues():
    good = MediaInfo("a", "mp4", 10, 1, 1, [VideoStream(0, "h264", 1920, 1080, 25)], [AudioStream(1, "aac", 48000, 2)])
    assert find_issues(good) == []
    bad = MediaInfo("a", "mp4", 0, 1, 1, [VideoStream(0, "h264", 0, 0, 0)], [])
    issues = find_issues(bad)
    assert len(issues) == 4
