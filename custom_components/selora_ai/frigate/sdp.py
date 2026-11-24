"""A stream's picture size, read from the SDP a camera answers DESCRIBE with.

The size is in the video's sequence parameter set, which RTSP cameras put in
the SDP (``sprop-parameter-sets`` for H.264, ``sprop-sps`` for H.265), so it is
known without decoding a frame or handing the stream's login to another
program. Some cameras also say it in an attribute (``a=framesize``,
``a=x-dimensions``), read when the SPS is missing.

The SDP comes from a device on the LAN: every read is bounded, and a malformed
one gives no size rather than an error.
"""

from __future__ import annotations

import base64
import re
from typing import Final

# Profiles whose SPS carries the chroma format and bit depths (H.264 7.3.2.1.1).
_H264_HIGH_PROFILES: Final = frozenset(
    {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}
)
# Sizes are at most five ASCII digits: a camera's line is untrusted, and int()
# refuses a number past Python's digit limit.
_FRAMESIZE: Final = re.compile(
    r"^a=framesize:\s*[0-9]+\s+([0-9]{1,5})-([0-9]{1,5})\s*$", re.IGNORECASE
)
_X_DIMENSIONS: Final = re.compile(
    r"^a=x-dimensions:\s*([0-9]{1,5})\s*,\s*([0-9]{1,5})\s*$", re.IGNORECASE
)
_MAX_SIDE: Final = 16384


class _Bits:
    """An exp-Golomb bit reader over an RBSP; reading past the end raises."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def u(self, count: int) -> int:
        value = 0
        for _ in range(count):
            byte = self._pos >> 3
            if byte >= len(self._data):
                raise ValueError("past the end of the SPS")
            value = (value << 1) | ((self._data[byte] >> (7 - (self._pos & 7))) & 1)
            self._pos += 1
        return value

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("malformed exp-Golomb code")
        return (1 << zeros) - 1 + self.u(zeros)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value & 1 else -(value // 2)


def _rbsp(nal: bytes) -> bytes:
    """The NAL unit without its emulation-prevention bytes (00 00 03)."""
    out = bytearray()
    zeros = 0
    for byte in nal:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def _skip_scaling_list(bits: _Bits, size: int) -> None:
    last = following = 8
    for _ in range(size):
        if following:
            following = (last + bits.se() + 256) % 256
        last = following or last


def h264_size(sps: bytes) -> tuple[int, int]:
    """Width and height from an H.264 SPS NAL unit (header byte included)."""
    bits = _Bits(_rbsp(sps[1:]))
    profile = bits.u(8)
    bits.u(16)  # constraint flags, level
    bits.ue()  # seq_parameter_set_id
    chroma = 1
    if profile in _H264_HIGH_PROFILES:
        chroma = bits.ue()
        if chroma == 3:
            bits.u(1)  # separate_colour_plane_flag
        bits.ue()  # bit_depth_luma_minus8
        bits.ue()  # bit_depth_chroma_minus8
        bits.u(1)  # qpprime_y_zero_transform_bypass_flag
        if bits.u(1):  # seq_scaling_matrix_present_flag
            for i in range(12 if chroma == 3 else 8):
                if bits.u(1):
                    _skip_scaling_list(bits, 16 if i < 6 else 64)
    bits.ue()  # log2_max_frame_num_minus4
    order = bits.ue()
    if order == 0:
        bits.ue()
    elif order == 1:
        bits.u(1)
        bits.se()
        bits.se()
        for _ in range(min(bits.ue(), 255)):
            bits.se()
    bits.ue()  # max_num_ref_frames
    bits.u(1)  # gaps_in_frame_num_value_allowed_flag
    width = (bits.ue() + 1) * 16
    map_units = bits.ue() + 1
    frame_mbs_only = bits.u(1)
    height = (2 - frame_mbs_only) * map_units * 16
    if not frame_mbs_only:
        bits.u(1)  # mb_adaptive_frame_field_flag
    bits.u(1)  # direct_8x8_inference_flag
    if bits.u(1):  # frame_cropping_flag
        left, right, top, bottom = bits.ue(), bits.ue(), bits.ue(), bits.ue()
        crop_x = 2 if chroma in (1, 2) else 1
        crop_y = (2 if chroma == 1 else 1) * (2 - frame_mbs_only)
        width -= (left + right) * crop_x
        height -= (top + bottom) * crop_y
    return width, height


def h265_size(sps: bytes) -> tuple[int, int]:
    """Width and height from an H.265 SPS NAL unit (both header bytes included)."""
    bits = _Bits(_rbsp(sps[2:]))
    bits.u(4)  # sps_video_parameter_set_id
    sub_layers = bits.u(3)  # sps_max_sub_layers_minus1
    bits.u(1)  # sps_temporal_id_nesting_flag
    # profile_tier_level: the general profile (88 bits) and level (8).
    bits.u(88)
    bits.u(8)
    present = [(bits.u(1), bits.u(1)) for _ in range(sub_layers)]
    if sub_layers:
        bits.u(2 * (8 - sub_layers))  # reserved_zero_2bits
    for profile_present, level_present in present:
        if profile_present:
            bits.u(88)
        if level_present:
            bits.u(8)
    bits.ue()  # sps_seq_parameter_set_id
    chroma = bits.ue()
    if chroma == 3 and bits.u(1):  # separate_colour_plane_flag
        chroma = 0
    width = bits.ue()
    height = bits.ue()
    if bits.u(1):  # conformance_window_flag
        left, right, top, bottom = bits.ue(), bits.ue(), bits.ue(), bits.ue()
        width -= (left + right) * (2 if chroma in (1, 2) else 1)
        height -= (top + bottom) * (2 if chroma == 1 else 1)
    return width, height


def _fmtp(line: str) -> dict[str, str]:
    _, _, params = line.partition(" ")
    pairs = (p.strip().partition("=") for p in params.split(";"))
    return {k.strip().lower(): v.strip() for k, _, v in pairs if k.strip()}


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4))


def _plausible(size: tuple[int, int]) -> tuple[int, int] | None:
    width, height = size
    return size if 0 < width <= _MAX_SIDE and 0 < height <= _MAX_SIDE else None


def video_size(sdp: str) -> tuple[int, int] | None:
    """The first video stream's width and height, or None when it says neither."""
    in_video = False
    fallback: tuple[int, int] | None = None
    for raw in sdp.splitlines():
        line = raw.strip()
        if line.startswith("m="):
            if in_video:
                break
            in_video = line.startswith("m=video")
            continue
        if not in_video:
            continue
        if line.lower().startswith("a=fmtp:"):
            params = _fmtp(line)
            try:
                if sps := params.get("sprop-sps"):
                    return _plausible(h265_size(_decode(sps)))
                if sets := params.get("sprop-parameter-sets"):
                    return _plausible(h264_size(_decode(sets.split(",")[0])))
            except ValueError:  # binascii.Error is one
                continue
        elif fallback is None and (match := _FRAMESIZE.match(line) or _X_DIMENSIONS.match(line)):
            fallback = _plausible((int(match.group(1)), int(match.group(2))))
    return fallback
