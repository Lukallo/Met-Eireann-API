"""The OpenAPI description must match the real routes and the parameters they accept."""


def test_home_redirects_to_docs(client):
    resp = client.get("/")
    assert resp.status_code == 302 and resp.headers["Location"] == "/docs"


def test_docs_page_loads_the_spec(client):
    html = client.get("/docs").get_data(as_text=True)
    assert "swagger-ui" in html and '"/v1/openapi.json"' in html


def test_spec_matches_routes(app, client):
    resp = client.get("/v1/openapi.json")
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    spec = resp.get_json()
    assert spec["openapi"].startswith("3.")
    routes = {}
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/v1/") and rule.rule not in ("/v1/", "/v1/openapi.json"):
            routes[rule.rule.replace("<station_id>", "{station_id}")] = rule.endpoint
    assert set(spec["paths"]) == set(routes)

    for path, endpoint in routes.items():
        accepted = set(app.view_functions[endpoint].accepted_params)
        documented = {p["name"] for p in spec["paths"][path]["get"].get("parameters", [])
                      if p["in"] == "query"}
        assert documented == accepted, path


def test_spec_references_resolve(client):
    spec = client.get("/v1/openapi.json").get_json()
    schemas = spec["components"]["schemas"]

    def refs(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref":
                    yield value
                else:
                    yield from refs(value)
        elif isinstance(node, list):
            for item in node:
                yield from refs(item)

    for ref in refs(spec):
        assert ref.startswith("#/components/schemas/") and ref.rsplit("/", 1)[1] in schemas, ref


def test_public_server_is_listed(client):
    servers = [s["url"] for s in client.get("/v1/openapi.json").get_json()["servers"]]
    assert "https://api.lcvetkovic.com" in servers
