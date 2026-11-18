from markdown_it import MarkdownIt

ALLOWED_LINK_SCHEMES = ("http://", "https://", "mailto:")
SAFE_LINK_REL = "noopener noreferrer"


def _is_allowed_link(url: str) -> bool:
    return url.strip().lower().startswith(ALLOWED_LINK_SCHEMES)


def _open_link_in_new_tab(renderer, tokens, index, options, env):
    tokens[index].attrSet("rel", SAFE_LINK_REL)
    tokens[index].attrSet("target", "_blank")
    return renderer.renderToken(tokens, index, options, env)


def _build_renderer() -> MarkdownIt:
    renderer = MarkdownIt("js-default", {"breaks": True, "html": False, "linkify": False})
    renderer.disable("image")
    renderer.validateLink = _is_allowed_link
    renderer.add_render_rule("link_open", _open_link_in_new_tab)
    return renderer


_RENDERER = _build_renderer()


def render_markdown(text: str) -> str:
    if not text.strip():
        return ""
    return _RENDERER.render(text)
