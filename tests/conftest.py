def pytest_configure(config):
    config.addinivalue_line(
        "markers", "story(us, ac): 이 테스트가 검증하는 스토리/인수조건 번호 (예: story('US-03', 'AC-2'))"
    )
