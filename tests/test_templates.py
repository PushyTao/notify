from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.markdown import render_markdown


@pytest.fixture
def templates():
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parents[1] / "app" / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["datetime"] = lambda value: "2026-11-14 10:30"
    env.filters['markdown'] = render_markdown
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


@pytest.mark.parametrize('page', ['index.html', 'record.html'])
def test_summary_markdown_renders_on_list_and_detail(templates, record, page):
    record['summary'] = '# 市场观察\n\n**重点**与*解释*\n\n- 放量\n- 缩量\n\n1. 先观察\n2. 再确认\n\n> 作者观点\n\n| 指标 | 数值 |\n| --- | --- |\n| 成交额 | 100 亿 |\n\n```text\n示例 <数据>\n```\n\n[来源](https://example.com/report)'
    record['transcript'] = '**这段是语音原文**\n不做格式转换'
    markup = templates.get_template(page).render(
        record=record, records=[record], authors=[], author_id='', page=1, pages=1, total=1)
    soup = BeautifulSoup(markup, 'html.parser')
    summary = soup.select_one('.record-field.summary dd')
    assert summary.select_one('strong').text == '重点'
    assert summary.select_one('h1').text == '市场观察'
    assert summary.select_one('em').text == '解释'
    assert [li.text for li in summary.select('ul > li')] == ['放量', '缩量']
    assert len(summary.select('ol > li')) == 2
    assert '作者观点' in summary.select_one('blockquote').text
    assert summary.select_one('table td').text == '成交额'
    assert '示例 <数据>' in summary.select_one('pre code').text
    assert summary.select_one('a')['href'] == 'https://example.com/report'
    assert soup.select_one('.transcript strong') is None
    assert record['transcript'] in soup.select_one('.transcript').text


@pytest.mark.parametrize('page', ['index.html', 'record.html'])
def test_summary_markdown_cannot_inject_html_or_unsafe_links(templates, record, page):
    record['summary'] = '''**安全文字**
<script>alert(1)</script>
<img src=x onerror=alert(2)>
<iframe src="https://evil.example"></iframe>
[点击](javascript:alert%281%29)
[实体](jav&#x61;script:alert%281%29)
[文件](file:///etc/passwd)
![远程图片](https://evil.example/tracker.png)
'''
    markup = templates.get_template(page).render(
        record=record, records=[record], authors=[], author_id='', page=1, pages=1, total=1)
    summary = BeautifulSoup(markup, 'html.parser').select_one('.record-field.summary dd')
    assert summary.select_one('strong').text == '安全文字'
    assert summary.select_one('script, img, iframe, object, svg') is None
    for link in summary.select('a[href]'):
        assert not link['href'].lower().startswith(('javascript:', 'vbscript:', 'file:', 'data:'))
    assert '<script>alert(1)</script>' in summary.text


def test_administration_form_labels(templates):
    assert "login.html" in templates.list_templates(), "Administrator login template is missing"
    assert "admin.html" in templates.list_templates(), "Administrator dashboard template is missing"
    login = BeautifulSoup(templates.get_template("login.html").render(), "html.parser")
    assert login.select_one("input[name=password]")["type"] == "password"
    admin = BeautifulSoup(templates.get_template("admin.html").render(csrf="example"), "html.parser")
    assert [tab.get_text(strip=True) for tab in admin.select("[role=tab]")] == [
        "概览", "博主", "订阅者", "模型与提示词", "设置", "任务"
    ]
