import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from keep2notes.enex import Options, enex_date, note_title, note_xml, write_enex
from keep2notes.html_clean import enml_to_text, html_to_enml, plain_to_enml, text_to_enml
from keep2notes.smoke import smoke_notes, tiny_png
from keep2notes.tags import label_to_tag, labels_to_tags
from keep2notes.verify import compare_titles


@pytest.fixture
def notes(tmp_path):
    return {n.source: n for n in smoke_notes(tmp_path)}


def parse_note(xml: str):
    note = ET.fromstring(xml)
    content = note.findtext("content")
    en_note = ET.fromstring(content.split("?>", 1)[1].split(">", 1)[1])  # drop XML decl + DOCTYPE
    return note, en_note, content


def test_every_smoke_note_is_valid_xml(notes, tmp_path):
    path = write_enex(list(notes.values()), tmp_path / "Smoke.enex")
    root = ET.parse(path).getroot()
    assert root.tag == "en-export"
    assert len(root.findall("note")) == 8
    for n in root.findall("note"):
        children = [c.tag for c in n]
        assert children[:4] == ["title", "content", "created", "updated"]


def test_checklist_uses_div_en_todo_with_state(notes):
    _, en_note, _ = parse_note(note_xml(notes["smoke-1"]))
    divs = en_note.findall("div")
    todos = [d.find("en-todo") for d in divs]
    assert all(t is not None for t in todos)
    assert [t.get("checked") for t in todos] == ["false", "true", "false"]
    assert en_note.find(".//li") is None
    assert divs[2].find("b").text == "Bold item"


def test_rich_text_bold_heading_and_blank_line(notes):
    _, en_note, content = parse_note(note_xml(notes["smoke-2"]))
    assert "style=" not in content and "class=" not in content and "<span" not in content
    bolds = [b.text for b in en_note.iter("b")]
    assert bolds == ["Heading", "Bold line"]
    assert "<div><br/></div>" in content


def test_image_resource_hash_matches_media(notes):
    xml = note_xml(notes["smoke-3"])
    note, en_note, _ = parse_note(xml)
    media = en_note.find(".//en-media")
    res = note.find("resource")
    assert media.get("type") == "image/png"
    assert media.get("hash") == hashlib.md5(tiny_png()).hexdigest()
    assert res.find("data").get("encoding") == "base64"
    assert res.findtext("mime") == "image/png"
    assert res.findtext("resource-attributes/file-name") == "k2n-smoke.png"


def test_tags_emitted(notes):
    note, _, _ = parse_note(note_xml(notes["smoke-4"]))
    assert [t.text for t in note.findall("tag")] == ["Gym-💪", "Outdoors-🚵🎢🏂🏄", "Movies-Shows-🎥🍿", "self-care", "🔒"]
    stripped = ET.fromstring(note_xml(notes["smoke-4"], Options(strip_emoji_tags=True)))
    assert [t.text for t in stripped.findall("tag")] == ["Gym", "Outdoors", "Movies-Shows", "self-care", "lock"]


@pytest.mark.parametrize(
    "label,tag,plain",
    [
        ("Friends 🙋\u200d♂️", "Friends-🙋\u200d♂️", "Friends"),
        ("Japan 🇯🇵", "Japan-🇯🇵", "Japan"),
        ("Ideas 💡 w AI 🧠", "Ideas-💡-w-AI-🧠", "Ideas-w-AI"),
        ("Go Green 🟢", "Go-Green-🟢", "Go-Green"),
        ("tennis", "tennis", "tennis"),
    ],
)
def test_label_to_tag(label, tag, plain):
    assert label_to_tag(label) == tag
    assert label_to_tag(label, strip_emoji=True) == plain


def test_labels_dedupe():
    assert labels_to_tags(["A b", "A  b", "A-b"]) == ["A-b"]


def test_links_annotations_and_autolink(notes):
    _, en_note, content = parse_note(note_xml(notes["smoke-5"]))
    hrefs = [a.get("href") for a in en_note.iter("a")]
    assert hrefs == ["https://example.com/a?b=1&c=2", "https://www.apple.com/"]
    assert "Already in text" not in content


def test_empty_title_uses_first_line(notes):
    assert note_title(notes["smoke-6"]) == "K2N 6 first line becomes title"


def test_escaping_and_cdata_end(notes):
    xml = note_xml(notes["smoke-7"])
    note, en_note, content = parse_note(xml)
    assert note.findtext("title") == "K2N 7 Escaping <&> ]]>"
    text = enml_to_text(ET.tostring(en_note, encoding="unicode"))
    assert "Less < greater > amp & cdata-end ]]> quotes" in text


def test_dates_are_utc(notes):
    n = notes["smoke-8"]
    note, _, _ = parse_note(note_xml(n))
    local = datetime(2019, 1, 1, 23, 30).astimezone()
    assert note.findtext("created") == local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    assert re.fullmatch(r"\d{8}T\d{6}Z", note.findtext("updated"))


def test_enex_date_format():
    assert enex_date(datetime(2020, 5, 6, 7, 8, 9, tzinfo=timezone.utc)) == "20200506T070809Z"


def test_html_cleaner_drops_unknown_and_keeps_text():
    out = html_to_enml('<p><span style="font-weight:400">a</span><font color="red">b</font></p>')
    assert out == "<div>ab</div>"


def test_plain_to_enml_blank_lines_and_spaces():
    assert plain_to_enml("a\n\n  b") == "<div>a</div><div><br/></div><div> \u00a0b</div>"


def test_text_to_enml_url_trailing_punctuation():
    assert text_to_enml("see https://x.com/a).") == 'see <a href="https://x.com/a">https://x.com/a</a>).'


def test_compare_titles():
    r = compare_titles(["A", "B", "B"], ["B", "A", "C"])
    assert r.missing == ["B"] and r.extra == ["C"] and not r.ok
    assert compare_titles(["x  y"], ["x y"]).ok
