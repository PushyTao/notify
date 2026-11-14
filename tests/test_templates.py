from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape


@pytest.fixture
def templates():
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parents[1] / "app" / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["datetime"] = lambda value: "2026-11-14 10:30"
    env.globals['asset_versions'] = {}
    return env


@pytest.fixture
def record():
    return {
        "id": 1,
        "published_at": 1794623400,
        "author_name": "博主甲",
        "summary": '<script>alert("summary")</script>',
        "transcript": '<img src=x onerror="alert(1)">\n完整转写',
        "title": "原始标题",
        "url": "https://www.douyin.com/video/12345",
    }


@pytest.mark.parametrize("page", ["index.html", "record.html"])
def test_public_record_field_order_and_escaping(templates, record, page):
    assert page in templates.list_templates(), "Public record template is missing"
    markup = templates.get_template(page).render(
        record=record, records=[record], authors=[], author_id="", page=1, pages=1, total=1
    )
    soup = BeautifulSoup(markup, "html.parser")
    assert [term.get_text(strip=True) for term in soup.select("article dt")] == [
        "更新时间", "博主名称", "总结内容", "内容全文", "原视频标题", "原视频链接"
    ]
    assert soup.find("article").find(["script", "img"]) is None
    assert record["summary"] in soup.get_text()
    assert record["transcript"] in soup.get_text()
    assert record["url"] in [link["href"] for link in soup.select("article a[href]")]
    assert all(not link["href"].startswith(("/admin", "/login")) for link in soup.select("a[href]"))
    if page == "index.html":
        assert record["transcript"] in soup.select_one("article details").get_text()


def test_public_index_preserves_filter_when_paging(templates):
    assert "index.html" in templates.list_templates(), "Public index template is missing"
    result = templates.get_template("index.html").render(
        records=[], authors=[{"id": 3, "name": "博主乙"}],
        author_id="3", page=2, pages=3, total=32,
    )
    soup = BeautifulSoup(result, "html.parser")
    assert soup.select_one("select[name=author_id] option[selected]")["value"] == "3"
    assert [a["href"] for a in soup.select("nav[aria-label='分页'] a")] == [
        "/?page=1&author_id=3", "/?page=3&author_id=3"
    ]


def test_administration_form_labels(templates):
    assert "login.html" in templates.list_templates(), "Administrator login template is missing"
    assert "admin.html" in templates.list_templates(), "Administrator dashboard template is missing"
    login = BeautifulSoup(templates.get_template("login.html").render(), "html.parser")
    assert login.select_one("input[name=password]")["type"] == "password"
    admin = BeautifulSoup(templates.get_template("admin.html").render(csrf="example"), "html.parser")
    assert [tab.get_text(strip=True) for tab in admin.select("[role=tab]")] == [
        "概览", "博主", "订阅者", "模型与提示词", "设置", "任务"
    ]
