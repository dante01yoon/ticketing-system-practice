"""11장 부하 테스트: 대기열에 서서 입장권을 받고, 내 차례를 묻고, 좌석맵을 새로고침하는 사용자."""
import uuid

from locust import HttpUser, between, task


class Fan(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.h = {"X-User-Id": f"fan-{uuid.uuid4().hex[:8]}"}
        self.cid = self.client.get("/concerts", headers=self.h).json()["items"][0]["id"]
        me = self.client.post(f"/concerts/{self.cid}/queue", headers=self.h, name="/queue").json()
        self.h["X-Entry-Token"] = me["entry_token"]

    @task(5)
    def my_turn(self):
        self.client.get(f"/concerts/{self.cid}/queue/me", headers=self.h, name="/queue/me")

    @task(3)
    def seat_map(self):
        self.client.get(f"/concerts/{self.cid}/seats", headers=self.h, name="/seats")

    @task(1)
    def concerts(self):
        self.client.get("/concerts", headers=self.h)
