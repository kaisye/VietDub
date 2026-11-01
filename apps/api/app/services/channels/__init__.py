"""Multi-channel management: OAuth connections, sync and dashboard aggregates.

Layout:
    crypto.py       Fernet encryption of tokens / client secrets at rest
    credentials.py  per-platform OAuth client id/secret (settings file + env fallback)
    oauth.py        PKCE + state, authorize URL, callback token exchange, refresh, revoke
    providers/      one class per platform behind a common interface
    sync.py         background sync (profile, stats snapshot, recent videos)
    manual.py       manual channels, manual metrics and CSV import
    dashboard.py    aggregate dashboard payload
"""

PLATFORMS = ("youtube", "facebook", "tiktok")
