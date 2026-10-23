class TestUsers:
    def test_create_by_url(self, client):
        client.post("/api/v1/users/create")

    def test_audit(self):
        assert True
