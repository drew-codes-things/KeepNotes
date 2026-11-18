from keepnotes.markdown_render import render_markdown


def test_renders_common_markdown():
    html = render_markdown("# Shopping\n\n**milk** and _eggs_\n\n- one\n- two\n\n`code`")
    assert "<h1>Shopping</h1>" in html
    assert "<strong>milk</strong>" in html
    assert "<em>eggs</em>" in html
    assert "<li>one</li>" in html
    assert "<code>code</code>" in html


def test_raw_html_is_escaped_not_run():
    html = render_markdown("<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>")
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_script_links_are_not_turned_into_links():
    for target in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "vbscript:x", "file:///etc/passwd"):
        assert "<a" not in render_markdown(f"[click]({target})"), target


def test_web_links_open_in_a_new_tab_without_referrer():
    html = render_markdown("[site](https://example.com/page)")
    assert 'href="https://example.com/page"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html


def test_mailto_links_are_allowed():
    assert 'href="mailto:me@example.com"' in render_markdown("[mail](mailto:me@example.com)")


def test_images_are_not_loaded_from_other_sites():
    html = render_markdown("![tracker](https://evil.example/pixel.png)")
    assert "<img" not in html


def test_single_newline_becomes_a_line_break():
    assert "<br>" in render_markdown("first line\nsecond line")


def test_empty_text_renders_nothing():
    assert render_markdown("") == ""
    assert render_markdown("   \n ") == ""


def test_attribute_breakout_in_link_titles_is_escaped():
    html = render_markdown('[x](https://example.com "a\\" onmouseover=\\"alert(1)")')
    assert 'onmouseover="' not in html
