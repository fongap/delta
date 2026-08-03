from coworker.delta_sources import SourceStore


def test_local_source_has_a_stable_hash_and_citation(tmp_path):
    source = tmp_path / "brief.md"
    source.write_text("# Brief\nLocal evidence")
    store = SourceStore(tmp_path / "sources.db")

    registered = store.register("notebook-1", source)
    citation = store.citation("notebook-1", source, "line 2")

    assert registered["content_hash"] == citation["content_hash"]
    assert citation["locator"] == "line 2"
