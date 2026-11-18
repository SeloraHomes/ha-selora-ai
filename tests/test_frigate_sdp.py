"""Tests for reading a stream's picture size from the SDP a camera answers."""

from __future__ import annotations

import base64

import pytest

from custom_components.selora_ai.frigate.sdp import h264_size, h265_size, video_size

# Sequence parameter sets x264 and x265 wrote for one frame of each size.
_H264 = {
    (2880, 1620): "Z2QAMqzZQC0AzfnwEQAAAwABAAADAAIPGDGW",  # High
    (1280, 720): "Z0LAH9kAUAW7ARAAAAMAEAAAAwAg8YMkgA==",  # Baseline
    (352, 288): "Z2QADKzZQWCWwEQAAAMABAAAAwAIPFCmWA==",  # Uniview's CIF third stream
    (1920, 1080): "Z2QAKKzZQHgCJ+XARAAAAwAEAAADAAg8YMZY",  # coded 1088, cropped
}
_H265 = {
    (3840, 2160): "QgEBAWAAAAMAkAAAAwAAAwCWoAHgIAIcWWVmkkyvAWgIAAADAAgAAAMACEA=",
    (2880, 1620): "QgEBAWAAAAMAkAAAAwAAAwCWoAFoIAZZ95ZWaSTK8BaAgAAAAwCAAAADAIQ=",
    (640, 360): "QgEBAWAAAAMAkAAAAwAAAwA/oAUCAWlllZpJMrwFoCAAAAMAIAAAAwAh",
}


@pytest.mark.parametrize(("size", "sps"), _H264.items())
def test_h264_size(size: tuple[int, int], sps: str) -> None:
    assert h264_size(base64.b64decode(sps)) == size


@pytest.mark.parametrize(("size", "sps"), _H265.items())
def test_h265_size(size: tuple[int, int], sps: str) -> None:
    assert h265_size(base64.b64decode(sps)) == size


def _sdp(video: str) -> str:
    return (
        "v=0\r\no=- 0 0 IN IP4 192.168.1.60\r\ns=Media Server\r\nt=0 0\r\n"
        "m=video 0 RTP/AVP 96\r\n" + video + "m=audio 0 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n"
    )


def test_an_h265_camera_says_its_size_in_sprop_sps() -> None:
    sdp = _sdp(
        "a=rtpmap:96 H265/90000\r\n"
        f"a=fmtp:96 sprop-vps=QAEMAf//; sprop-sps={_H265[(2880, 1620)]}; sprop-pps=RAHA8vA8kA==\r\n"
    )

    assert video_size(sdp) == (2880, 1620)


def test_an_h264_camera_says_its_size_in_sprop_parameter_sets() -> None:
    sdp = _sdp(
        "a=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 packetization-mode=1;profile-level-id=640032;"
        f"sprop-parameter-sets={_H264[(352, 288)]},aOvjyyLA\r\n"
    )

    assert video_size(sdp) == (352, 288)


def test_without_an_sps_the_framesize_attribute_is_read() -> None:
    sdp = _sdp("a=rtpmap:96 H264/90000\r\na=framesize:96 1280-720\r\n")

    assert video_size(sdp) == (1280, 720)


def test_a_broken_sps_falls_back_to_the_framesize() -> None:
    sdp = _sdp(
        "a=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 sprop-parameter-sets=Z2Q=,aOvjyyLA\r\na=x-dimensions:640,360\r\n"
    )

    assert video_size(sdp) == (640, 360)


def test_an_sdp_that_does_not_say_has_no_size() -> None:
    assert video_size(_sdp("a=rtpmap:96 H264/90000\r\n")) is None
    assert video_size("RTSP garbage") is None
    # The audio section's attributes are not the video's.
    assert video_size("m=audio 0 RTP/AVP 96\r\na=framesize:96 1280-720\r\n") is None
