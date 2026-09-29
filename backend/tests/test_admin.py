from fastapi.testclient import TestClient

from app import server

client = TestClient(server.app)
H = {"X-Admin-Token": server.ADMIN_TOKEN}


def test_admin_requires_token():
    assert client.get("/api/admin/rooms").status_code == 401
    assert client.get("/api/admin/rooms", headers={"X-Admin-Token": "nope"}).status_code == 401


def test_admin_lists_and_deletes_rooms_and_kicks_players():
    r = client.post("/api/rooms", json={"player_name": "A", "private": True}).json()
    rooms = client.get("/api/admin/rooms", headers=H).json()
    assert any(x["code"] == r["code"] and x["private"] for x in rooms)  # private rooms are visible to admin
    with client.websocket_connect(f"/ws/{r['code']}?token={r['token']}") as ws:
        assert ws.receive_json()["t"] == "state"
        assert client.delete(f"/api/admin/rooms/{r['code']}", headers=H).json() == {"deleted": r["code"]}
        msg = ws.receive_json()
        assert msg["t"] == "error" and msg["fatal"]
    assert all(x["code"] != r["code"] for x in client.get("/api/admin/rooms", headers=H).json())
    assert client.delete(f"/api/admin/rooms/{r['code']}", headers=H).status_code == 404
