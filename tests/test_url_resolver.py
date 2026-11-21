from backend.url_resolver import (
    extract_download_link_from_html,
    extract_gdrive_confirm_url,
    resolve_url_strategy,
)


def test_resolve_google_drive_view_url():
    res = resolve_url_strategy("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view?usp=sharing")
    assert res.adapter_name == "google_drive"
    assert "id=1AbCdEfGhIjKlMnOpQrStUvWxYz" in res.download_url
    assert "confirm=t" in res.download_url


def test_resolve_google_docs_url():
    res = resolve_url_strategy("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit")
    assert res.adapter_name == "google_docs"
    assert res.download_url.endswith("/export?format=pdf")
    assert res.suggested_filename == "1AbCdEfGhIjKlMnOpQrStUvWxYz.pdf"


def test_resolve_dropbox_and_onedrive():
    db = resolve_url_strategy("https://www.dropbox.com/s/xyz123/chapter1.pdf?dl=0")
    assert db.adapter_name == "dropbox"
    assert "dl=1" in db.download_url

    od = resolve_url_strategy("https://onedrive.live.com/redir?resid=12345")
    assert od.adapter_name == "onedrive"
    assert "download=1" in od.download_url


def test_extract_gdrive_confirm_form():
    sample_html = """
    <html><body>
    <form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
      <input type="hidden" name="id" value="FILE1234567890">
      <input type="hidden" name="export" value="download">
      <input type="hidden" name="confirm" value="t">
      <input type="hidden" name="uuid" value="abcd-efgh">
    </form>
    </body></html>
    """
    confirm = extract_gdrive_confirm_url(sample_html)
    assert confirm is not None
    assert "id=FILE1234567890" in confirm
    assert "uuid=abcd-efgh" in confirm


def test_extract_download_link_from_html_page():
    html_page = """
    <html><body>
      <h1>Tai lieu Chuong 1</h1>
      <a class="btn" href="/assets/audio/chuong_01.mp3">Tai MP3</a>
    </body></html>
    """
    link = extract_download_link_from_html(html_page, "https://books.example.com/ch1")
    assert link == "https://books.example.com/assets/audio/chuong_01.mp3"
