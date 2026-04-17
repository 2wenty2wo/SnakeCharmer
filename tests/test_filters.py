from app.filters import apply_filters
from app.models import ShowFilters
from app.trakt import TraktShow


class TestApplyFilters:
    def test_no_filters_allows_all(self):
        show = TraktShow(title="Anything", tvdb_id=1)
        filters = ShowFilters()
        assert apply_filters(show, filters) == (True, None)

    def test_blacklisted_genres_blocks_match(self):
        show = TraktShow(title="Reality Show", tvdb_id=1, genres=["reality", "drama"])
        filters = ShowFilters(blacklisted_genres=["reality"])
        assert apply_filters(show, filters) == (False, "filtered_by_genre")

    def test_blacklisted_genres_case_insensitive(self):
        show = TraktShow(title="Reality Show", tvdb_id=1, genres=["Reality"])
        filters = ShowFilters(blacklisted_genres=["reality"])
        assert apply_filters(show, filters) == (False, "filtered_by_genre")

    def test_blacklisted_genres_no_genres_passes(self):
        show = TraktShow(title="Unknown", tvdb_id=1, genres=[])
        filters = ShowFilters(blacklisted_genres=["reality"])
        assert apply_filters(show, filters) == (True, None)

    def test_blacklisted_networks_blocks_match(self):
        show = TraktShow(title="YouTube Show", tvdb_id=1, network="YouTube")
        filters = ShowFilters(blacklisted_networks=["youtube"])
        assert apply_filters(show, filters) == (False, "filtered_by_network")

    def test_blacklisted_networks_no_network_passes(self):
        show = TraktShow(title="Mystery", tvdb_id=1, network=None)
        filters = ShowFilters(blacklisted_networks=["youtube"])
        assert apply_filters(show, filters) == (True, None)

    def test_blacklisted_min_year_blocks_old_show(self):
        show = TraktShow(title="Old Show", tvdb_id=1, year=2005)
        filters = ShowFilters(blacklisted_min_year=2010)
        assert apply_filters(show, filters) == (False, "filtered_by_year")

    def test_blacklisted_max_year_blocks_new_show(self):
        show = TraktShow(title="Future Show", tvdb_id=1, year=2030)
        filters = ShowFilters(blacklisted_max_year=2025)
        assert apply_filters(show, filters) == (False, "filtered_by_year")

    def test_year_filter_passes_when_no_year(self):
        show = TraktShow(title="Unknown Year", tvdb_id=1, year=None)
        filters = ShowFilters(blacklisted_min_year=2010, blacklisted_max_year=2020)
        assert apply_filters(show, filters) == (True, None)

    def test_blacklisted_title_keywords_blocks(self):
        show = TraktShow(title="Untitled Project", tvdb_id=1)
        filters = ShowFilters(blacklisted_title_keywords=["untitled"])
        assert apply_filters(show, filters) == (False, "filtered_by_title")

    def test_blacklisted_title_keywords_case_insensitive(self):
        show = TraktShow(title="The UNTITLED Show", tvdb_id=1)
        filters = ShowFilters(blacklisted_title_keywords=["untitled"])
        assert apply_filters(show, filters) == (False, "filtered_by_title")

    def test_blacklisted_tvdb_ids_blocks(self):
        show = TraktShow(title="Blocked", tvdb_id=123)
        filters = ShowFilters(blacklisted_tvdb_ids=[123])
        assert apply_filters(show, filters) == (False, "filtered_by_tvdb_id")

    def test_allowed_countries_blocks_missing_country(self):
        show = TraktShow(title="No Country", tvdb_id=1, country=None)
        filters = ShowFilters(allowed_countries=["us"])
        assert apply_filters(show, filters) == (False, "filtered_by_country")

    def test_allowed_countries_allows_with_ignore(self):
        show = TraktShow(title="No Country", tvdb_id=1, country=None)
        filters = ShowFilters(allowed_countries=["us", "ignore"])
        assert apply_filters(show, filters) == (True, None)

    def test_allowed_countries_blocks_wrong_country(self):
        show = TraktShow(title="Foreign Show", tvdb_id=1, country="fr")
        filters = ShowFilters(allowed_countries=["us", "gb"])
        assert apply_filters(show, filters) == (False, "filtered_by_country")

    def test_allowed_countries_allows_matching_country(self):
        show = TraktShow(title="US Show", tvdb_id=1, country="US")
        filters = ShowFilters(allowed_countries=["us"])
        assert apply_filters(show, filters) == (True, None)

    def test_allowed_countries_empty_allows_any(self):
        show = TraktShow(title="Any Show", tvdb_id=1, country="xx")
        filters = ShowFilters(allowed_countries=[])
        assert apply_filters(show, filters) == (True, None)

    def test_allowed_languages_blocks_missing_language(self):
        show = TraktShow(title="No Language", tvdb_id=1, language=None)
        filters = ShowFilters(allowed_languages=["en"])
        assert apply_filters(show, filters) == (False, "filtered_by_language")

    def test_allowed_languages_allows_with_ignore(self):
        show = TraktShow(title="No Language", tvdb_id=1, language=None)
        filters = ShowFilters(allowed_languages=["en", "ignore"])
        assert apply_filters(show, filters) == (True, None)

    def test_allowed_languages_blocks_wrong_language(self):
        show = TraktShow(title="Foreign Show", tvdb_id=1, language="fr")
        filters = ShowFilters(allowed_languages=["en"])
        assert apply_filters(show, filters) == (False, "filtered_by_language")

    def test_combined_filters_first_match_wins(self):
        show = TraktShow(
            title="Bad Title",
            tvdb_id=1,
            year=2000,
            genres=["reality"],
            network="youtube",
        )
        filters = ShowFilters(
            blacklisted_genres=["reality"],
            blacklisted_networks=["youtube"],
            blacklisted_min_year=2010,
            blacklisted_title_keywords=["bad"],
        )
        # genre should be the first match
        assert apply_filters(show, filters) == (False, "filtered_by_genre")

    def test_show_passes_all_filters(self):
        show = TraktShow(
            title="Good Drama",
            tvdb_id=1,
            year=2020,
            genres=["drama"],
            network="hbo",
            country="us",
            language="en",
        )
        filters = ShowFilters(
            blacklisted_genres=["reality"],
            blacklisted_networks=["youtube"],
            blacklisted_min_year=2010,
            blacklisted_max_year=2025,
            blacklisted_title_keywords=["untitled"],
            blacklisted_tvdb_ids=[999],
            allowed_countries=["us"],
            allowed_languages=["en"],
        )
        assert apply_filters(show, filters) == (True, None)
