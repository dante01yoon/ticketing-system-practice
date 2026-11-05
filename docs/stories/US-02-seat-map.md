# US-02 좌석맵 보기

**사용자로서** 공연의 좌석과 각 좌석의 상태를 보고 싶다. **그래서** 잡을 수 있는 좌석을 고를 수 있다.

## 인수 조건
**AC-1 좌석과 상태 조회**
- Given 공연 C에 좌석 A1(AVAILABLE), A2(HELD, 만료 전), A3(SOLD)가 있다
- When 공연 C의 좌석맵을 요청한다
- Then 세 좌석이 좌석 ID·좌석 번호와 함께 각각 AVAILABLE, HELD, SOLD로 반환된다

**AC-2 만료된 홀드는 AVAILABLE로 보인다**
- Given 좌석 A2가 6분 전에 홀드되었다
- When 공연 C의 좌석맵을 요청한다
- Then A2는 AVAILABLE로 보인다

**AC-3 없는 공연**
- Given 공연 X가 존재하지 않는다
- When 공연 X의 좌석맵을 요청한다
- Then "공연 없음"으로 거절된다

**AC-4 개인정보 비노출**
- Given 좌석 A2를 사용자 u1이 홀드했다
- When 다른 사용자가 좌석맵을 요청한다
- Then 응답에 홀드한 사용자 ID는 들어 있지 않다
