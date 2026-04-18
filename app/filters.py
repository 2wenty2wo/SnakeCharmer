from app.models import ShowFilters
from app.trakt import TraktShow


def apply_filters(show: TraktShow, filters: ShowFilters) -> tuple[bool, str | None]:
    """Evaluate show against filters. Returns (should_include, reason)."""
    # Blacklisted genres
    if filters.blacklisted_genres and show.genres:
        blacklisted = {g.lower() for g in filters.blacklisted_genres}
        if any(genre.lower() in blacklisted for genre in show.genres):
            return False, "filtered_by_genre"

    # Blacklisted networks
    if filters.blacklisted_networks and show.network:
        blacklisted = {n.lower() for n in filters.blacklisted_networks}
        if show.network.lower() in blacklisted:
            return False, "filtered_by_network"

    # Year range
    if show.year is not None:
        if filters.blacklisted_min_year is not None and show.year < filters.blacklisted_min_year:
            return False, "filtered_by_year"
        if filters.blacklisted_max_year is not None and show.year > filters.blacklisted_max_year:
            return False, "filtered_by_year"

    # Title keywords
    if filters.blacklisted_title_keywords:
        title_lower = show.title.lower()
        for keyword in filters.blacklisted_title_keywords:
            if keyword.lower() in title_lower:
                return False, "filtered_by_title"

    # TVDB IDs
    if filters.blacklisted_tvdb_ids and show.tvdb_id in filters.blacklisted_tvdb_ids:
        return False, "filtered_by_tvdb_id"

    # Allowed countries
    if filters.allowed_countries:
        allowed = {c.lower() for c in filters.allowed_countries}
        if "ignore" not in allowed and (
            show.country is None or show.country.lower() not in allowed
        ):
            return False, "filtered_by_country"

    # Allowed languages
    if filters.allowed_languages:
        allowed = {c.lower() for c in filters.allowed_languages}
        if "ignore" not in allowed and (
            show.language is None or show.language.lower() not in allowed
        ):
            return False, "filtered_by_language"

    return True, None
