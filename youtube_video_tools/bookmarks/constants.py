"""SponsorBlock categories shared by scanning, planning and redownload."""

MARK_CATEGORIES = "all"
REMOVE_CATEGORIES = "sponsor,interaction,selfpromo"
REMOVE_CATEGORY_SET = frozenset(REMOVE_CATEGORIES.split(","))
