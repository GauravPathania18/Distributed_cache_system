from services.telemetry import RequestTelemetry


def test_starts_empty():

    t = RequestTelemetry()
    s = t.stats()

    assert s["gets_cache"] == 0
    assert s["gets_database"] == 0
    assert s["gets_miss"] == 0
    assert s["search_cache"] == 0
    assert s["search_database"] == 0
    assert s["cache_reads"] == 0
    assert s["db_reads"] == 0
    assert s["hit_rate_pct"] == 0.0


def test_record_get_classifies_sources():

    t = RequestTelemetry()

    t.record_get("cache")
    t.record_get("cache")
    t.record_get("database")
    t.record_get("miss")

    s = t.stats()

    assert s["gets_cache"] == 2
    assert s["gets_database"] == 1
    assert s["gets_miss"] == 1


def test_record_search_classifies_hits():

    t = RequestTelemetry()

    t.record_search(hit=True)
    t.record_search(hit=False)

    s = t.stats()

    assert s["search_cache"] == 1
    assert s["search_database"] == 1


def test_db_load_reduction_math():

    t = RequestTelemetry()

    for _ in range(8):
        t.record_search(hit=True)
    for _ in range(2):
        t.record_search(hit=False)

    s = t.stats()

    assert s["cache_reads"] == 8
    assert s["db_reads"] == 2
    assert s["db_reads_avoided"] == 8
    assert s["served_reads"] == 10
    assert s["hit_rate_pct"] == 80.0
    assert s["db_load_reduction_pct"] == 80.0


def test_mixed_gets_and_searches_combine():

    t = RequestTelemetry()

    t.record_get("cache")
    t.record_search(hit=True)
    t.record_get("database")

    s = t.stats()

    assert s["cache_reads"] == 2
    assert s["db_reads"] == 1
    assert s["hit_rate_pct"] == round(2 / 3 * 100, 2)


def test_reset_zeroes_everything():

    t = RequestTelemetry()

    t.record_get("cache")
    t.record_search(hit=False)
    t.record_set()
    t.record_delete()
    t.reset()

    s = t.stats()

    assert s["gets_cache"] == 0
    assert s["search_database"] == 0
    assert s["sets"] == 0
    assert s["deletes"] == 0
    assert s["db_reads"] == 0


def test_sets_and_deletes_counted():

    t = RequestTelemetry()

    t.record_set()
    t.record_set()
    t.record_delete()

    s = t.stats()

    assert s["sets"] == 2
    assert s["deletes"] == 1
