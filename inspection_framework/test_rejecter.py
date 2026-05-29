"""
test_rejecter.py — Rejecter 동작 검증 테스트

실제 하드웨어 없이 카메라를 mock으로 대체하여 로직만 검증합니다.

테스트 항목:
    T1.  Individual - 웜업 기간에는 리젝트 없음
    T2.  Continuous - BURST START: _cont_on_time / _cont_deadline 정확히 설정
    T3.  Continuous - 즉시 연속 불량 → deadline 연장 (단일 스레드 유지, merge)
    T4.  Continuous - burst 완료 후 두 번째 불량 → 새 burst (gap)
    T5.  Continuous - 실제 valve ON → OFF 확인 (ema=0으로 keepalive 고정)
    T6.  Individual - 기본 발사 및 valve ON → OFF 확인
    T7.  reject_positions 클램프 (deque 크기 초과 방지)
    T8.  Continuous delay=0 + positions=3 → IndexError 없음
    T9.  worker-01 실제 설정 재현: continuous + delay=0, positions=3
    T10. Continuous merge 시나리오: 두 번째 push가 deadline 연장하는지 확인
    T11. Continuous gap 시나리오: burst 완료 후 push → 새 on_time > 이전 deadline
    T12. reset() 후 윈도우와 발사 상태 완전 초기화
    T13. Individual positions=3 → 불량 1개에서 최대 3발 발사
    T14. window_state 프로퍼티가 현재 윈도우를 올바르게 반환
    T15. 터미널에 INFO 로그가 출력되지 않아야 함
"""

import sys
import os
import io
import logging
import time

from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rejecter import Rejecter


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def make_rejecter(**kwargs) -> tuple[Rejecter, MagicMock]:
    """카메라를 mock으로 대체한 Rejecter 생성."""
    cam = MagicMock()
    defaults = dict(
        reject_delay_frames=5,
        reject_positions=1,
        reject_mode="continuous",
        time_valve_on=0.05,
        pre_valve_delay=0.0,
        debug=False,
    )
    defaults.update(kwargs)
    return Rejecter(camera=cam, **defaults), cam


def push_n(r: Rejecter, n: int, is_defect: bool = False) -> None:
    for _ in range(n):
        r.push(is_defect=is_defect)


def set_stable_ema(r: Rejecter) -> None:
    """EMA를 0으로 설정해 keepalive = time_valve_on 고정 (테스트 타이밍 예측 가능)."""
    r._frame_period_ema = 0.0


def wait_for_burst_end(r: Rejecter, timeout: float = 2.0) -> bool:
    """burst 스레드가 종료될 때까지 최대 timeout초 대기. 종료되면 True."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with r._cont_lock:
            if not r._cont_thread_running:
                return True
        time.sleep(0.01)
    return False


# ── T1: Individual - 웜업 기간 리젝트 없음 ────────────────────────────────────

def test_warmup_no_fire():
    """
    Individual 모드 웜업 기간(첫 reject_delay_frames 프레임)에는
    불량을 넣어도 리젝트가 발생하지 않아야 한다.
    """
    r, _ = make_rejecter(
        reject_delay_frames=5, reject_positions=1, reject_mode="individual",
    )

    for i in range(5):
        fired = r.push(is_defect=True)
        assert fired == [], (
            f"T1 FAIL: warmup {i + 1}번째 프레임에서 fire 발생 (fired={fired})"
        )

    print("✓ T1 PASS: Individual 웜업 기간 동안 리젝트 없음")


# ── T2: Continuous - BURST START 타이밍 검증 ─────────────────────────────────

def test_continuous_burst_start_timing():
    """
    delay=0 조건에서 불량 push 후 _cont_on_time ≈ now + pre_valve_delay,
    _cont_deadline ≈ _cont_on_time + time_valve_on 이어야 한다.
    """
    pre = 0.10
    valve = 0.30
    r, _ = make_rejecter(
        reject_delay_frames=0, reject_mode="continuous",
        pre_valve_delay=pre, time_valve_on=valve,
    )
    set_stable_ema(r)  # keepalive = valve_on

    t0 = time.time()
    r.push(is_defect=True)
    t1 = time.time()

    with r._cont_lock:
        on_t = r._cont_on_time
        deadline = r._cont_deadline

    tol = 0.03
    assert t0 + pre - tol <= on_t <= t1 + pre + tol, (
        f"T2 FAIL: on_time={on_t - t0:.3f}s (기대 ≈ {pre}s)"
    )
    assert abs(deadline - on_t - valve) < tol, (
        f"T2 FAIL: deadline - on_time={deadline - on_t:.3f}s (기대 ≈ {valve}s)"
    )

    print("✓ T2 PASS: BURST START — on_time / deadline 정확히 설정")


# ── T3: Continuous - merge (즉시 연속 불량 → deadline 연장) ──────────────────

def test_continuous_merge_extends_deadline():
    """
    두 번째 push가 첫 번째 burst 도중(스레드 실행 중)에 오면
    새 스레드 없이 deadline만 연장되어야 한다.
    """
    r, _ = make_rejecter(
        reject_delay_frames=0, reject_mode="continuous",
        pre_valve_delay=0.0, time_valve_on=0.5,
    )
    set_stable_ema(r)

    r.push(is_defect=True)
    with r._cont_lock:
        deadline_1 = r._cont_deadline
        assert r._cont_thread_running, "T3 FAIL: burst 스레드가 시작되지 않음"

    time.sleep(0.02)
    r.push(is_defect=True)  # 스레드 실행 중 → EXTEND 경로

    with r._cont_lock:
        deadline_2 = r._cont_deadline
        thread_still_running = r._cont_thread_running

    assert deadline_2 > deadline_1, (
        f"T3 FAIL: deadline 미연장 (d1={deadline_1:.4f}, d2={deadline_2:.4f})"
    )
    assert thread_still_running, "T3 FAIL: 스레드가 중복 생성되었거나 종료됨"

    print("✓ T3 PASS: 즉시 연속 불량 → deadline 연장 (단일 스레드 유지)")


# ── T4: Continuous - gap (burst 완료 후 새 burst 시작) ───────────────────────

def test_continuous_gap_creates_new_burst():
    """
    첫 번째 burst가 완전히 끝난 뒤(스레드 종료 확인) 두 번째 불량이 오면
    새 burst가 시작되어야 한다. 두 번째 on_time > 첫 번째 deadline → gap 확인.
    """
    r, _ = make_rejecter(
        reject_delay_frames=0, reject_mode="continuous",
        pre_valve_delay=0.0, time_valve_on=0.05,
    )
    set_stable_ema(r)

    r.push(is_defect=True)
    with r._cont_lock:
        deadline_1 = r._cont_deadline

    assert wait_for_burst_end(r, timeout=1.0), "T4 FAIL: burst 스레드가 1초 안에 종료되지 않음"

    r.push(is_defect=True)  # 새 burst 시작
    with r._cont_lock:
        on_t_2 = r._cont_on_time

    assert on_t_2 >= deadline_1, (
        f"T4 FAIL: 두 번째 on_time({on_t_2:.4f}) < 첫 번째 deadline({deadline_1:.4f}) → gap 아님"
    )

    print("✓ T4 PASS: burst 완료 후 새 불량 → 새 burst 시작 (gap 확인)")


# ── T5: Continuous - 실제 valve ON → OFF 확인 ────────────────────────────────

def test_continuous_valve_fires_and_turns_off():
    """
    ema=0 으로 keepalive = time_valve_on 고정 후
    실제 set_reject_output(True) → set_reject_output(False) 호출 확인.
    """
    r, cam = make_rejecter(
        reject_delay_frames=0, reject_mode="continuous",
        pre_valve_delay=0.0, time_valve_on=0.06,
    )
    set_stable_ema(r)

    r.push(is_defect=True)
    time.sleep(0.02)
    cam.set_reject_output.assert_called_with(True)

    assert wait_for_burst_end(r, timeout=1.0), "T5 FAIL: burst 스레드가 1초 안에 종료되지 않음"
    time.sleep(0.02)
    cam.set_reject_output.assert_called_with(False)

    print("✓ T5 PASS: Continuous valve ON → OFF 동작 확인")


# ── T6: Individual - 기본 발사 및 valve ON → OFF ─────────────────────────────

def test_individual_fire():
    """
    Individual 모드에서 마크가 zone에 진입하면 fire가 시작되고,
    valve_on 후 OFF 되어야 한다.

    설정: delay=3, positions=1, window_size=4
    push 순서: warmup×3 → defect×1 → normal×2 → normal×1(fire)
    """
    r, cam = make_rejecter(
        reject_delay_frames=3,
        reject_positions=1,
        reject_mode="individual",
        time_valve_on=0.1,
    )

    push_n(r, 3)                            # warmup (push_count 1-3)
    r.push(is_defect=True)                  # push_count=4, mark at index 0
    push_n(r, 2)                            # mark shifts to index 2
    fired = r.push(is_defect=False)         # mark at index 3 = zone[-1] → fire

    assert fired == [-1], f"T6 FAIL: expected [-1], got {fired}"

    time.sleep(0.05)
    cam.set_reject_output.assert_called_with(True)

    time.sleep(0.15)
    cam.set_reject_output.assert_called_with(False)

    print("✓ T6 PASS: Individual 모드 발사 및 valve ON → OFF 확인")


# ── T7: reject_positions 클램프 ───────────────────────────────────────────────

def test_reject_positions_clamped():
    """
    reject_positions는 deque 크기(reject_delay_frames + 1)를 초과할 수 없다.
    초과하면 window[-reject_positions]가 IndexError를 발생시킨다.
    """
    cases = [
        # (delay, positions_입력, positions_기대값)
        (0, 3, 1),   # continuous 강제 delay=0 시나리오 (실제 버그 조건)
        (0, 1, 1),   # delay=0, positions=1 → 그대로
        (5, 3, 3),   # delay=5 → window size=6, positions=3 ≤ 6 → 그대로
        (5, 6, 6),   # delay=5 → positions=6 = maxlen → 그대로
        (5, 7, 6),   # delay=5 → positions=7 > maxlen=6 → 클램프
        (2, 10, 3),  # delay=2 → maxlen=3, positions=10 → 클램프
    ]
    for delay, pos_in, pos_expected in cases:
        r, _ = make_rejecter(reject_delay_frames=delay, reject_positions=pos_in)
        assert r.reject_positions == pos_expected, (
            f"T7 FAIL: delay={delay}, positions_입력={pos_in} → "
            f"실제={r.reject_positions}, 기대={pos_expected}"
        )
    print("✓ T7 PASS: reject_positions 클램프 동작 확인 (모든 케이스)")


# ── T8: Continuous delay=0 + positions=3 → IndexError 없음 ───────────────────

def test_continuous_delay0_positions3_no_index_error():
    """
    수정 전 버그: continuous 모드에서 delay=0(maxlen=1), positions=3이면
    range(-3, 0)이 window[-3]을 참조해 IndexError 발생 → 워커 크래시.
    수정 후: __init__에서 positions=min(3, 0+1)=1로 클램프.
    """
    r, cam = make_rejecter(
        reject_delay_frames=0,
        reject_positions=3,
        reject_mode="continuous",
        time_valve_on=0.05,
    )

    assert r.reject_positions == 1, (
        f"T8 FAIL: 클램프 미적용 — reject_positions={r.reject_positions} (기대: 1)"
    )

    try:
        r.push(is_defect=True)
        r.push(is_defect=True)
        r.push(is_defect=False)
    except IndexError as e:
        assert False, f"T8 FAIL: IndexError 발생 — {e}"

    print("✓ T8 PASS: delay=0, positions=3(클램프→1) 조건에서 IndexError 없음")


# ── T9: worker-01 실제 설정 재현 ──────────────────────────────────────────────

def test_worker01_continuous_scenario():
    """
    worker-01 config.json의 실제 값과 동일한 조건을 재현합니다.
    inspection_worker가 continuous 모드에서 delay_frames를 0으로 강제한 뒤
    Rejecter를 생성합니다.
    """
    config_reject_positions = 3
    config_delay_frames     = 10
    forced_delay            = 0   # continuous 모드 강제값

    r, cam = make_rejecter(
        reject_delay_frames=forced_delay,
        reject_positions=config_reject_positions,
        reject_mode="continuous",
        time_valve_on=1.0,
        pre_valve_delay=0.5,
    )

    expected_positions = min(config_reject_positions, forced_delay + 1)
    assert r.reject_positions == expected_positions, (
        f"T9 FAIL: reject_positions={r.reject_positions} (기대: {expected_positions})"
    )

    try:
        for i in range(50):
            r.push(is_defect=(i % 7 == 0))
    except IndexError as e:
        assert False, f"T9 FAIL: 50프레임 push 중 IndexError — {e}"
    except Exception as e:
        assert False, f"T9 FAIL: 예상치 못한 예외 — {type(e).__name__}: {e}"

    print(
        f"✓ T9 PASS: worker-01 실제 설정 재현 — "
        f"delay=0, positions={config_reject_positions}→{expected_positions}, "
        f"50프레임 push 정상 완료"
    )


# ── T10: Merge 시나리오 — 두 번째 push가 deadline 연장 ───────────────────────

def test_continuous_merge_scenario():
    """
    pre_delay=0.05, valve_on=0.10 설정에서:
    t=0   push → on=t0+0.05, deadline=t0+0.15
    t=0.03 push → EXTEND: new_deadline ≈ t0+0.18 > t0+0.15 → 연장(merge)
    """
    r, _ = make_rejecter(
        reject_delay_frames=0, reject_mode="continuous",
        pre_valve_delay=0.05, time_valve_on=0.10,
    )
    set_stable_ema(r)

    r.push(is_defect=True)
    with r._cont_lock:
        deadline_1 = r._cont_deadline

    time.sleep(0.03)
    r.push(is_defect=True)

    with r._cont_lock:
        deadline_2 = r._cont_deadline
        still_running = r._cont_thread_running

    assert deadline_2 > deadline_1, (
        f"T10 FAIL: deadline 미연장 (d1={deadline_1:.4f}, d2={deadline_2:.4f})"
    )
    assert still_running, "T10 FAIL: 스레드가 종료됨 (새 스레드 시작으로 잘못 분기)"

    print("✓ T10 PASS: Merge 시나리오 — deadline 연장 확인")


# ── T11: Gap 시나리오 — burst 완료 후 두 번째 burst on_time > 첫 deadline ─────

def test_continuous_gap_scenario():
    """
    pre_delay=0.0, valve_on=0.05 설정:
    첫 burst deadline = now+0.05 → burst 완료 후 두 번째 push
    두 번째 on_time >= 첫 deadline → 실질적 gap 확인.
    """
    r, _ = make_rejecter(
        reject_delay_frames=0, reject_mode="continuous",
        pre_valve_delay=0.0, time_valve_on=0.05,
    )
    set_stable_ema(r)

    r.push(is_defect=True)
    with r._cont_lock:
        deadline_1 = r._cont_deadline

    assert wait_for_burst_end(r, timeout=1.0), "T11 FAIL: 첫 번째 burst가 종료되지 않음"

    time.sleep(0.01)  # burst 완전 종료 후 push
    r.push(is_defect=True)
    with r._cont_lock:
        on_t_2 = r._cont_on_time

    assert on_t_2 >= deadline_1, (
        f"T11 FAIL: 두 번째 on_time({on_t_2:.4f}) < 첫 deadline({deadline_1:.4f})"
    )

    print("✓ T11 PASS: Gap 시나리오 — 새 burst on_time이 이전 deadline 이후")


# ── T12: reset() 초기화 검증 ──────────────────────────────────────────────────

def test_reset_clears_state():
    """
    reset() 후 윈도우는 전부 0, push_count=0, 발사 상태 초기화,
    camera.set_reject_output(False) 호출되어야 한다.
    """
    r, cam = make_rejecter(
        reject_delay_frames=3, reject_positions=1, reject_mode="individual",
    )

    push_n(r, 4)
    r.push(is_defect=True)

    r.reset()

    assert all(v == 0 for v in r.window_state), (
        f"T12 FAIL: reset 후 윈도우에 1이 남아있음: {r.window_state}"
    )
    assert r._push_count == 0, (
        f"T12 FAIL: push_count 미초기화 ({r._push_count})"
    )
    with r._fire_lock:
        assert r._active_fires == 0, "T12 FAIL: active_fires 미초기화"
    with r._cont_lock:
        assert not r._cont_thread_running, "T12 FAIL: cont_thread_running 미초기화"

    cam.set_reject_output.assert_called_with(False)

    print("✓ T12 PASS: reset() — 윈도우·카운터·발사 상태·valve OFF 초기화 확인")


# ── T13: Individual positions=3 → 불량 1개에서 최대 3발 ─────────────────────

def test_individual_positions3_fires_multiple():
    """
    delay=5, positions=3, individual 모드에서
    불량 1개가 zone을 통과할 때 3개 위치([-3], [-2], [-1])에서 순차 발사.
    """
    r, cam = make_rejecter(
        reject_delay_frames=5,
        reject_positions=3,
        reject_mode="individual",
        pre_valve_delay=0.0,
        time_valve_on=0.05,
    )

    push_n(r, 5)               # warmup (push_count 1-5)
    r.push(is_defect=True)     # push_count=6: mark at 0
    push_n(r, 2)               # mark shifts to index 2
    fired_3 = r.push(is_defect=False)   # mark enters zone[-3] → fire 1
    fired_2 = r.push(is_defect=False)   # zone[-2] → fire 2
    fired_1 = r.push(is_defect=False)   # zone[-1] → fire 3

    all_fired = fired_3 + fired_2 + fired_1
    assert len(all_fired) == 3, (
        f"T13 FAIL: 발사 횟수={len(all_fired)} (기대=3), fired={all_fired}"
    )
    assert sorted(all_fired) == [-3, -2, -1], (
        f"T13 FAIL: 발사 위치 불일치 (기대=[-3,-2,-1], 실제={sorted(all_fired)})"
    )

    time.sleep(0.15)
    cam.set_reject_output.assert_called_with(False)

    print("✓ T13 PASS: Individual positions=3 → 불량 1개에서 3발 발사")


# ── T14: window_state 프로퍼티 ────────────────────────────────────────────────

def test_window_state_reflects_pushes():
    """
    window_state 프로퍼티가 슬라이딩 윈도우 현재 상태를 정확히 반환하는지 확인.
    """
    r, _ = make_rejecter(
        reject_delay_frames=4, reject_positions=1, reject_mode="individual",
    )

    push_n(r, 4)  # warmup: window = [0,0,0,0,0]
    state_after_warmup = r.window_state
    assert all(v == 0 for v in state_after_warmup), (
        f"T14 FAIL: 웜업 후 윈도우에 0이 아닌 값: {state_after_warmup}"
    )

    r.push(is_defect=True)  # mark at index 0
    state_with_mark = r.window_state
    assert state_with_mark[0] == 1, (
        f"T14 FAIL: 불량 push 후 window[0] != 1: {state_with_mark}"
    )
    assert state_with_mark.count(1) == 1, (
        f"T14 FAIL: 1이 1개 이상 존재: {state_with_mark}"
    )

    assert len(state_with_mark) == 5, (
        f"T14 FAIL: 윈도우 길이={len(state_with_mark)} (기대=5)"
    )

    print("✓ T14 PASS: window_state — 마크 위치 및 크기 정확히 반환")


# ── T15: 터미널 INFO 로그 없음 확인 ──────────────────────────────────────────

def test_no_info_logs_in_terminal():
    """
    Rejecter 동작 중 logging 모듈에 INFO 레코드가 발생하지 않아야 한다.
    ppocr / ppdet 로거는 WARNING 이상으로 설정되어 있어야 한다.

    터미널에 PaddleOCR INFO 스팸이 표시되는 근본 원인:
      detector_paddleocr.__init__ 에서 PaddleOCR(**kwargs) 호출 이전에
      logging.getLogger('ppocr').setLevel(WARNING) 을 설정해야 한다.
      (이후 설정 시 init 중 INFO 메시지가 이미 출력됨)
    """
    # ─ Part 1: Rejecter 동작 중 INFO 로그 포착 여부 ─────────────────────────
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.addHandler(handler)
    try:
        r, _ = make_rejecter(
            reject_delay_frames=3, reject_positions=1, reject_mode="individual",
        )
        push_n(r, 5)
        r.push(is_defect=True)
        push_n(r, 3)
        r.reset()
    finally:
        root.removeHandler(handler)

    captured = buf.getvalue().strip()
    assert not captured, (
        f"T15 FAIL: Rejecter 동작 중 INFO 이상 로그 포착됨:\n{captured}"
    )

    # ─ Part 2: ppocr / ppdet 로거 레벨 확인 ────────────────────────────────
    # detector_paddleocr 모듈이 현재 세션에서 이미 임포트되었다면 레벨이 설정되어 있음.
    # 아직 임포트 전이라면 NOTSET(0) 이므로 스킵.
    ppocr_level = logging.getLogger("ppocr").level
    ppdet_level = logging.getLogger("ppdet").level

    if ppocr_level != logging.NOTSET:
        assert ppocr_level >= logging.WARNING, (
            f"T15 FAIL: ppocr 로거 레벨={ppocr_level} — WARNING({logging.WARNING}) 이상 필요"
        )
    if ppdet_level != logging.NOTSET:
        assert ppdet_level >= logging.WARNING, (
            f"T15 FAIL: ppdet 로거 레벨={ppdet_level} — WARNING({logging.WARNING}) 이상 필요"
        )

    # ─ Part 3: detector_paddleocr 모듈에서 로그 억제가 PaddleOCR init 전에 실행되는지 확인 ─
    # 소스 파일을 읽어 setLevel이 PaddleOCR(**ocr_kwargs) 앞에 있는지 순서 검증.
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "detector_paddleocr.py")
    if os.path.exists(src_path):
        with open(src_path) as f:
            lines = f.readlines()
        suppress_line = next(
            (i for i, l in enumerate(lines) if 'getLogger("ppocr").setLevel' in l), None
        )
        paddle_init_line = next(
            (i for i, l in enumerate(lines) if "PaddleOCR(**ocr_kwargs)" in l), None
        )
        if suppress_line is not None and paddle_init_line is not None:
            assert suppress_line < paddle_init_line, (
                f"T15 FAIL: ppocr 로그 억제(line {suppress_line + 1})가 "
                f"PaddleOCR 초기화(line {paddle_init_line + 1}) 이후에 있음 — "
                f"init 중 INFO 스팸 발생 가능"
            )

    print("✓ T15 PASS: 터미널 INFO 로그 없음 / ppocr·ppdet 억제 순서 확인")


# ── 실행 ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_warmup_no_fire,
    test_continuous_burst_start_timing,
    test_continuous_merge_extends_deadline,
    test_continuous_gap_creates_new_burst,
    test_continuous_valve_fires_and_turns_off,
    test_individual_fire,
    test_reject_positions_clamped,
    test_continuous_delay0_positions3_no_index_error,
    test_worker01_continuous_scenario,
    test_continuous_merge_scenario,
    test_continuous_gap_scenario,
    test_reset_clears_state,
    test_individual_positions3_fires_multiple,
    test_window_state_reflects_pushes,
    test_no_info_logs_in_terminal,
]

if __name__ == "__main__":
    passed = 0
    failed = 0

    print("=" * 60)
    print("Rejecter 동작 검증 테스트")
    print("=" * 60)

    for t in TESTS:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"✗ {t.__name__} ERROR: {e}")
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"결과: {passed}/{passed + failed} 통과")
    if failed == 0:
        print("✅ 모든 테스트 통과!")
    else:
        print(f"❌ {failed}개 실패")

    sys.exit(0 if failed == 0 else 1)
