from pathlib import Path


def test_execution_submit_uses_one_id_backed_fragment_fetch():
    source = Path("app/static/js/app.js").read_text(encoding="utf-8")
    start = source.index("document.addEventListener('execution-submitted'")
    end = source.index("// Swapping the Analyses section", start)
    handler = source[start:end]

    assert "e.detail.execution_id" in handler
    assert handler.count("htmx.ajax(") == 1
    assert "setInterval" not in handler
    assert "attempts" not in handler
    assert "htmx.trigger" not in handler
    assert handler.count('input[name="portfolio_ids"]:checked') == 1
