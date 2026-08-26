"""Regression tests for the hard-sub blur FFmpeg filter.

A box blur over a short caption band used to emit ``boxblur={r}:2`` with no
explicit chroma radius. FFmpeg defaults the chroma radius to the luma value, but
on 4:2:0 video the chroma plane is subsampled to a quarter area, so its ceiling
is ``min(w, h) // 4``. A strong blur therefore produced e.g. ``boxblur=38:2``
which FFmpeg rejected with "Invalid chroma_param radius value 38, must be >= 0
and <= 28", aborting the whole render. The blur op now clamps the luma and
chroma radii to their respective plane ceilings.
"""

import pytest

from app.services.renderer import _blur_op


def _radii(filter_str: str) -> tuple[int, int]:
    # boxblur=<luma>:<lpow>:<chroma>:<cpow>
    assert filter_str.startswith("boxblur="), filter_str
    parts = filter_str.split("=", 1)[1].split(":")
    return int(parts[0]), int(parts[2])


@pytest.mark.parametrize(
    "width, height, strength",
    [
        (1382, 112, 135),  # the exact reported failure (chroma ceiling 28)
        (1382, 112, 250),  # max strength on a short band
        (1382, 151, 50),   # ordinary 1080p caption band, auto strength
        (40, 16, 250),     # tiny region, extreme strength
        (200, 112, 200),   # narrow region (width is the limiting dimension)
    ],
)
def test_box_blur_radii_never_exceed_plane_ceilings(width, height, strength):
    luma, chroma = _radii(_blur_op("box", width, height, strength))
    smallest = min(width, height)
    assert 1 <= luma <= smallest // 2
    assert 1 <= chroma <= smallest // 4


def test_reported_failure_case_clamps_chroma_to_limit():
    # 112px-tall band → chroma ceiling 28; the old code emitted radius 38.
    assert _blur_op("box", 1382, 112, 135) == "boxblur=38:2:28:2"


def test_box_blur_unaffected_when_radius_is_within_limits():
    # Auto strength on a normal band stays below both ceilings → luma == chroma.
    luma, chroma = _radii(_blur_op("box", 1382, 151, 50))
    assert luma == chroma


def test_directional_blurs_still_use_gblur():
    assert _blur_op("vertical", 1382, 112, 135).startswith("gblur=sigma=0:sigmaV=")
    assert _blur_op("horizontal", 1382, 112, 135).startswith("gblur=sigma=")
