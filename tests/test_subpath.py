from minioj.server.main import app


def test_root_path_is_preserved_in_links_static_assets_and_redirects(client):
    original_root_path = app.root_path
    app.root_path = "/minioj"
    try:
        home = client.get("/")
        assert home.status_code == 200
        assert 'href="/minioj/problems"' in home.text
        assert 'href="http://testserver/minioj/static/style.css"' in home.text

        protected = client.get("/submissions", follow_redirects=False)
        assert protected.status_code == 303
        assert protected.headers["location"] == "/minioj/login"
    finally:
        app.root_path = original_root_path
