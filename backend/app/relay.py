"""상시 릴레이: python -m backend.app.relay"""
import logging
import os
import time

from .events import relay_once

log = logging.getLogger("relay")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    interval = float(os.environ.get("RELAY_INTERVAL_SECONDS", "1"))
    while True:
        try:
            n = relay_once()
            if n:
                log.info("sent %d", n)
                continue
        except Exception as e:  # noqa: BLE001  Kafka/DB 일시 오류: 다음 주기에 다시
            log.warning("relay failed: %r", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
