"""
rejecter.py — 리젝트 신호 관리 모듈
======================================

[역할]
    컨베이어 벨트 속도에 맞춰 "지연 리젝트"를 구현합니다.
    슬라이딩 윈도우(deque)로 불량 결과를 추적하고,
    window[-N:] 범위 안에 1이 있는 위치마다 독립적으로 신호를 발생시킵니다.

[슬라이딩 윈도우 개념]
    reject_delay_frames=10, reject_positions=3 예시:

    초기:         [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
                   ↑ index 0                    ↑ index -1
                   (최신)                        (가장 오래된)

    불량 감지 후 shift 7회:
                  [0, 0, 0, 0, 0, 0, 0, 1, 0, 0]  ← index[-3] → 발사 1
    shift 8회:    [0, 0, 0, 0, 0, 0, 0, 0, 1, 0]  ← index[-2] → 발사 2
    shift 9회:    [0, 0, 0, 0, 0, 0, 0, 0, 0, 1]  ← index[-1] → 발사 3

    reject_positions = 1 → window[-1:]만 체크 → 1번 발사 (기본 동작)
    reject_positions = 3 → window[-3:] 체크 → 최대 3번 발사

[커스터마이즈 포인트]
    - reject_delay_frames : 슬라이딩 윈도우 크기 (컨베이어 딜레이)
    - reject_positions    : window[-N:] 범위 크기. 이 범위 안 각 위치에서 독립 발사
    - time_valve_on       : 밸브 열림 지속 시간 [초] (예: 0.1, 0.2, 0.3)
    - pre_valve_delay      : 리젝트 신호 ON 전 추가 대기 시간 [초]
    - camera              : BaslerCamera 인스턴스 (신호 출력에 필요)

[insert_position 활용 예시]
    # 카메라 FOV 내 제품 위치로 삽입 위치를 계산해 전달
    y_ratio = detection.y_center / frame_height  # 0.0 ~ 1.0
    pos = int(y_ratio * reject_delay_frames)
    rejecter.push(is_defect=True, insert_position=pos)
"""

import time
import threading
from collections import deque
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from camera import BaslerCamera


class Rejecter:
    """
    슬라이딩 윈도우 기반 지연 리젝트 신호 관리 클래스.

    [동작 원리]
        1. push(is_defect, insert_position)을 매 프레임 호출합니다.
        2. 윈도우를 한 칸 shift(appendleft)하고, 불량이면 insert_position에 마킹합니다.
        3. window[-reject_positions:] 범위의 각 인덱스를 체크합니다.
        4. 1이 있는 인덱스마다 독립적으로 리젝트 신호를 발사합니다.

    사용법 예시
    -----------
    rejecter = Rejecter(
        camera=cam,
        reject_delay_frames=10,   # 윈도우 크기
        reject_positions=3,       # 뒤 3칸 체크 → 불량 1개당 최대 3번 발사
        time_valve_on=0.1,
        pre_valve_delay=0.25,
    )

    # 메인 루프 안에서:
    rejecter.push(is_defect=True)                     # 기본: 맨 앞(0)에 마킹
    rejecter.push(is_defect=True, insert_position=3)  # 앞에서 4번째에 마킹
    """

    def __init__(
        self,
        camera: "BaslerCamera",
        reject_delay_frames: int = 10,
        time_valve_on: float = 0.1,
        pre_valve_delay: float = 0.25,
        reject_positions: int = 1,
        reject_mode: str = "individual",
        debug: bool = False,
    ):
        """
        Parameters
        ----------
        camera               : BaslerCamera 인스턴스 (리젝트 신호 출력용)
        reject_delay_frames  : 슬라이딩 윈도우 크기.
                               컨베이어 속도와 카메라 위치에 따라 조정하세요.
        reject_positions     : window[-N:] 범위 크기.
                               1 = 맨 뒤 1칸만 체크 (기본, 1번 발사)
                               3 = 뒤 3칸 체크 (최대 3번 발사)
        reject_mode          : 리젝트 발사 방식.
                               "individual" = 각 위치마다 pre_valve_delay + time_valve_on 독립 발사.
                                              positions=3이면 -3, -2, -1 위치에서 각각 발사.
                               "continuous" = 윈도우[-N:]에 마크가 있는 동안 단일 burst로 ON 유지.
                                              마지막 마크 후 time_valve_on 뒤 OFF.
                                              연속 불량 시 burst가 자동 연장되어 끊김 없이 ON.
        time_valve_on        : 밸브 열림 지속 시간 [초] (예: 0.1, 0.2, 0.3).
        pre_valve_delay       : 신호 발생 직전 추가 대기 시간 [초].
                               에어건 등 기계 응답 지연 보상에 사용합니다.
        """
        self.camera = camera
        self.reject_delay_frames = reject_delay_frames
        # deque maxlen = delay_frames+1, so reject_positions must not exceed that.
        # continuous collection_mode forces delay_frames=0 → maxlen=1, clamp here
        # so individual-mode zone checks (range(-reject_positions, 0)) never go OOB.
        self.reject_positions = min(reject_positions, reject_delay_frames + 1)
        self.reject_mode = reject_mode
        self.time_valve_on = time_valve_on
        self.pre_valve_delay = pre_valve_delay
        self.debug = debug

        # 슬라이딩 윈도우: 0으로 초기화, maxlen으로 크기 고정
        # maxlen = delay_frames + 1: position -1이 정확히 delay_frames번 shift 후 발사되도록 보정
        self._window: deque = deque(
            [0] * (reject_delay_frames + 1), maxlen=reject_delay_frames + 1
        )
        # warm-up: delay_frames+1만큼 실제 데이터가 쌓인 후부터 체크
        self._push_count: int = 0

        self._lock = threading.Lock()
        self._firing_positions: set = set()  # 현재 발사 중인 인덱스 집합 (individual)
        self._fire_lock = threading.Lock()   # I/O 신호 ON/OFF 경쟁 조건 방지
        self._active_fires: int = 0          # 현재 발사 중인 스레드 수 (individual)

        # continuous 모드: deadline 기반 단일 burst
        # 스레드 하나만 실행하고, push()는 _cont_deadline만 연장.
        # 경쟁 조건(race condition) 없이 이중 발사를 방지합니다.
        self._cont_lock = threading.Lock()
        self._cont_deadline: float = -1e9   # 밸브 OFF 시각 (epoch)
        self._cont_on_time: float = -1e9    # 밸브 ON 예정 시각 (epoch)
        self._cont_thread_running: bool = False
        self._last_push_time: float = 0.0
        self._frame_period_ema: float = 1.0  # 프레임 주기 지수이동평균 (초)

    # ------------------------------------------------------------------
    # 공개 메서드 (Public Methods)
    # ------------------------------------------------------------------

    def push(self, is_defect: bool, insert_position: int = 0) -> List[int]:
        """
        검사 결과를 슬라이딩 윈도우에 추가하고, 리젝트 조건을 확인합니다.
        매 프레임 호출해야 합니다.

        Parameters
        ----------
        is_defect       : True이면 불량, False이면 정상
        insert_position : 윈도우에서 불량을 마킹할 인덱스 (기본 0=맨 앞).
                          카메라 FOV 내 제품 위치를 계산해 전달할 수 있습니다.

        Returns
        -------
        List[int] : 이번 호출에서 발사가 시작된 인덱스 목록 (예: [-3, -2])
        """
        fired = []
        warmup_exit = False
        zone_triggered = False
        _now = time.time()
        _frame_dt = _now - self._last_push_time
        self._last_push_time = _now
        if 0.0 < _frame_dt < 60.0:  # 첫 호출(dt≈epoch)과 긴 정지 구간 제외
            self._frame_period_ema = 0.2 * _frame_dt + 0.8 * self._frame_period_ema
        with self._lock:
            # 1. 윈도우를 한 칸 shift: 앞에 0 추가, 맨 뒤는 자동 제거
            self._window.appendleft(0)
            self._push_count += 1

            # 2. 불량이면 지정 위치에 마킹
            if is_defect:
                self._window[insert_position] = 1

            # 3. warm-up: delay_frames+1 이전에는 체크하지 않음
            if self._push_count < self.reject_delay_frames + 1:
                warmup_exit = True

            # 4. window[-N:] 범위 체크 — individual과 continuous 모두 sliding window 사용
            if not warmup_exit:
                if self.reject_mode != "continuous":
                    # ── Individual: 각 위치마다 pre_valve_delay + time_valve_on 독립 발사 ──
                    for i in range(-self.reject_positions, 0):
                        if self._window[i] == 1 and i not in self._firing_positions:
                            self._firing_positions.add(i)
                            fired.append(i)
                            threading.Thread(
                                target=self._fire_reject, args=(i,), daemon=True,
                            ).start()
                else:
                    # ── Continuous: 존 안에 마크가 하나라도 있으면 burst 트리거 ──
                    # individual과 동일하게 reject_delay_frames 딜레이 후에만 발동
                    zone_triggered = any(
                        self._window[i] == 1 for i in range(-self.reject_positions, 0)
                    )
                    if zone_triggered:
                        fired.append(-1)

        # ── Continuous burst 처리 (_lock 밖) ──
        if zone_triggered:
            now = time.time()
            # 프레임 주기의 2배를 최소 keepalive로 설정 → 다음 프레임이 오기 전에 burst가 끊기지 않음
            keepalive = max(self.time_valve_on, self._frame_period_ema * 2.0)
            with self._cont_lock:
                if self._cont_thread_running:
                    # 이미 실행 중 → deadline만 연장 (스레드 추가 생성 없음)
                    new_deadline = now + self.pre_valve_delay + keepalive
                    if new_deadline > self._cont_deadline:
                        self._cont_deadline = new_deadline
                    print(f"[Rejecter] EXTEND  {time.strftime('%H:%M:%S')}.{int(time.time() % 1 * 1000):03d}  deadline+{self._cont_deadline - now:.3f}s ema={self._frame_period_ema:.3f}s")
                else:
                    # 스레드 없음 → 새 burst 시작
                    self._cont_on_time = now + self.pre_valve_delay
                    self._cont_deadline = self._cont_on_time + keepalive
                    self._cont_thread_running = True
                    print(f"[Rejecter] BURST   {time.strftime('%H:%M:%S')}.{int(time.time() % 1 * 1000):03d}  on+{self.pre_valve_delay:.3f}s keepalive={keepalive:.3f}s ema={self._frame_period_ema:.3f}s")
                    threading.Thread(
                        target=self._continuous_burst_thread, daemon=True
                    ).start()

        if self.debug:
            self._print_debug(is_defect, fired, warmup_exit)

        return fired

    def _print_debug(self, is_defect: bool, fired: List[int], warmup: bool):
        """프레임별 상태를 터미널에 출력합니다 (debug=True 시 사용)."""
        with self._lock:
            win = list(self._window)
            push_num = self._push_count

        # 윈도우 바 문자열: 비존 구간은 그냥 나열, 존 앞에 | 삽입
        # 예) delay=10, positions=3 → [........|X..]
        total = len(win)
        zone_start = total - self.reject_positions
        bar = ""
        for i, v in enumerate(win):
            if i == zone_start:
                bar += "|"
            bar += "X" if v == 1 else "."
        win_str = f"[{bar}]"

        frame_label = "DEFECT" if is_defect else "normal"

        if warmup:
            print(f"[F {push_num:04d}] {frame_label:6s} {win_str} (warmup)")
            return

        # 밸브/burst 상태
        if self.reject_mode == "continuous":
            with self._cont_lock:
                running = self._cont_thread_running
                deadline = self._cont_deadline
                on_time = self._cont_on_time
            if running:
                now = time.time()
                if now < on_time:
                    valve_str = f"valve:WAIT({on_time - now:.2f}s)"
                else:
                    remaining = max(0.0, deadline - now)
                    valve_str = f"valve:ON({remaining:.2f}s) "
            else:
                valve_str = "valve:OFF     "
        else:
            with self._fire_lock:
                fires = self._active_fires
            valve_str = f"valve:{'ON ' if fires > 0 else 'OFF'} (fires={fires})"

        # 이번 프레임 이벤트
        event = ""
        if fired:
            if self.reject_mode == "continuous":
                event = " ← BURST START"
            else:
                event = f" ← FIRE {fired}"
        elif is_defect:
            event = " ← mark placed"

        print(f"[F {push_num:04d}] {frame_label:6s} {win_str} {valve_str}{event}")

    def reset(self):
        """윈도우와 리젝트 상태를 초기화합니다. 라인 정지/재시작 시 호출하세요."""
        with self._lock:
            self._window = deque(
                [0] * (self.reject_delay_frames + 1), maxlen=self.reject_delay_frames + 1
            )
            self._push_count = 0
            self._firing_positions = set()
        with self._fire_lock:
            self._active_fires = 0
        with self._cont_lock:
            self._cont_deadline = -1e9
            self._cont_on_time = -1e9
            self._cont_thread_running = False
        self._frame_period_ema = 1.0
        self._last_push_time = 0.0
        self.camera.set_reject_output(False)
        print("[Rejecter] Window reset.")

    @property
    def window_state(self) -> list:
        """현재 슬라이딩 윈도우 상태를 리스트로 반환합니다 (디버깅/UI 모니터링용)."""
        with self._lock:
            return list(self._window)

    # ------------------------------------------------------------------
    # 내부 메서드 (Internal Methods)
    # ------------------------------------------------------------------

    def _fire_reject(self, idx: int, duration: float = None):
        """Individual 모드: 한 위치를 ON → 대기 → OFF 합니다.

        카운터 방식으로 경쟁 조건을 방지합니다:
        - 여러 스레드가 동시에 ON을 요청해도 신호는 한 번만 ON 됩니다.
        - 마지막 스레드가 끝날 때만 OFF 하므로 중간에 끊기지 않습니다.

        Parameters
        ----------
        idx      : 슬라이딩 윈도우 인덱스 (로그/추적용)
        duration : 밸브 ON 지속 시간 [초]. None이면 self.time_valve_on 사용.
        """
        on_time = duration if duration is not None else self.time_valve_on
        try:
            time.sleep(self.pre_valve_delay)   # 기계 응답 딜레이 보상
            with self._fire_lock:
                self._active_fires += 1
                self.camera.set_reject_output(True)
            print(f"[Rejecter] REJECT ON (idx={idx}, {on_time:.3f}s)")
            time.sleep(on_time)  # 밸브 열림 지속 시간
        finally:
            with self._fire_lock:
                self._active_fires -= 1
                if self._active_fires == 0:
                    self.camera.set_reject_output(False)
                    print(f"[Rejecter] REJECT OFF (idx={idx})")
            with self._lock:
                self._firing_positions.discard(idx)

    def _continuous_burst_thread(self):
        """Continuous 모드 burst: deadline 기반 단일 ON/OFF 사이클.

        동작:
            1. _cont_on_time까지 대기 후 밸브 ON.
            2. _cont_deadline을 폴링 — push()가 deadline을 연장할 수 있음.
            3. deadline 만료 직전 이중 확인 후 밸브 OFF.
            4. 스레드 종료(_cont_thread_running = False).

        이중 확인(double-check):
            폴링 루프를 탈출하기 직전, lock을 잡고 deadline을 재확인합니다.
            push()가 탈출 시점에 deadline을 연장했다면 루프를 계속합니다.
            이로써 단일 스레드 보장 + 이중 발사 방지를 동시에 달성합니다.
        """
        try:
            with self._cont_lock:
                on_time = self._cont_on_time
            wait = on_time - time.time()
            if wait > 0:
                time.sleep(wait)
            with self._fire_lock:
                self.camera.set_reject_output(True)
            print(f"[Rejecter] CONT ON  {time.strftime('%H:%M:%S')}.{int(time.time() % 1 * 1000):03d}")

            while True:
                with self._cont_lock:
                    deadline = self._cont_deadline
                remaining = deadline - time.time()
                if remaining <= 0:
                    # 이중 확인: 탈출 직전에 deadline이 연장됐는지 재확인
                    with self._cont_lock:
                        if self._cont_deadline > time.time():
                            continue
                    break
                time.sleep(min(0.02, remaining))

            with self._fire_lock:
                self.camera.set_reject_output(False)
            print(f"[Rejecter] CONT OFF {time.strftime('%H:%M:%S')}.{int(time.time() % 1 * 1000):03d}")
        except Exception:
            with self._fire_lock:
                self.camera.set_reject_output(False)
            raise
        finally:
            with self._cont_lock:
                self._cont_thread_running = False
