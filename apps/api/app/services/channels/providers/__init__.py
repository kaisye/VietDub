from __future__ import annotations

from .base import ChannelProvider
from .facebook import FacebookProvider
from .tiktok import TikTokProvider
from .youtube import YouTubeProvider

PROVIDERS: dict[str, type[ChannelProvider]] = {
    "youtube": YouTubeProvider,
    "facebook": FacebookProvider,
    "tiktok": TikTokProvider,
}


def get_provider(platform: str, client=None) -> ChannelProvider:
    try:
        return PROVIDERS[platform](client)
    except KeyError:
        raise ValueError(f"Unknown platform: {platform}") from None
