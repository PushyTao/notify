"""Render model-generated Markdown without accepting executable HTML."""
from markdown_it import MarkdownIt
from markupsafe import Markup


# js-default enables tables/strikethrough and disables raw HTML. Keep images
# disabled so a generated summary cannot cause external tracking requests.
# The parser also rejects javascript:, file: and other dangerous link schemes.
_parser = MarkdownIt('js-default', {'html': False, 'breaks': True}).disable('image')


def render_markdown(text: str) -> Markup:
    return Markup(_parser.render(text or ''))
