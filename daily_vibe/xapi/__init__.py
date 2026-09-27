"""X (Twitter) API v2 helpers used by the built-in X Bookmarks plugin.

* ``net``    – tiny urllib transport (swappable in tests, no third-party deps)
* ``oauth``  – OAuth 2.0 Authorization Code + PKCE with a loopback redirect
* ``tokens`` – token storage: OS keyring, or a clearly-labeled 0600 fallback file
* ``client`` – authenticated API client: auto-refresh, pagination, 429 handling
* ``sync``   – per-journal seen-ID tracking, first-run policy, media download
* ``render`` – plain-Markdown output for bookmarked posts
"""
