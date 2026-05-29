"""
test_config.py — InspectionConfig 및 backend 설정 헬퍼 함수 검증

테스트 항목:
  ── InspectionConfig (inspection_framework/config.py) ──────────────────
  C1.  기본값 확인
  C2.  to_dict() rotation 변환: int → 문자열
  C3.  from_dict() rotation 변환: 문자열 → int
  C4.  Backward compat: reject_pulse_count → time_valve_on 자동 변환
  C5.  project_name 없으면 line_name으로 자동 채움
  C6.  products + active_product → 활성 제품 필드 flat 병합
  C7.  from_dict() 알 수 없는 키 무시 (TypeError 없음)
  C8.  to_dict() products 있을 때 flat product 필드 제거
  C9.  to_json() / from_json() 라운드트립

  ── backend 유틸 함수 (backend/main.py) ────────────────────────────────
  G1.  _load_global_settings: 파일 없음 → 기본값 반환
  G2.  _load_global_settings: 부분 파일 → 기본값과 병합
  G3.  _save_global_settings + _load_global_settings: atomic write 라운드트립
  G4.  _mask_secret: 빈 값, 짧은 키, 긴 키
  G5.  _migrate_config: flat 구버전 config → "Default" 제품 생성
  G6.  _migrate_config: products 이미 있으면 active_product 기본 설정
  G7.  _expand_product_fields: active 제품 필드를 flat 레벨로 복사
  G8.  _verify_password: 일치 / 불일치
"""

import json
import os
import sys
import tempfile

# ── 경로 설정 ────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "inspection_framework"))

from inspection_framework.config import InspectionConfig, PRODUCT_LEVEL_FIELDS
import backend.main as _main


# ═══════════════════════════════════════════════════════════════════════════════
# InspectionConfig 테스트
# ═══════════════════════════════════════════════════════════════════════════════

def test_c1_defaults():
    """기본값으로 생성 시 핵심 필드들이 올바른 기본값을 가져야 한다."""
    c = InspectionConfig()
    assert c.line_name == "inspection-line"
    assert c.camera_ip == "192.168.1.10"
    assert c.reject_delay_frames == 10
    assert c.reject_positions == 1
    assert c.reject_mode == "individual"
    assert c.time_valve_on == 0.1
    assert c.device == "cuda"
    assert c.rotation is None
    assert c.crop_region is None
    assert c.class_thresholds is None
    print("✓ C1 PASS: 기본값 확인")


def test_c2_to_dict_rotation():
    """to_dict()에서 rotation int 상수가 문자열로 변환되어야 한다."""
    c90cw = InspectionConfig(rotation=0)   # cv2.ROTATE_90_CLOCKWISE
    c180  = InspectionConfig(rotation=1)   # cv2.ROTATE_180
    c90cc = InspectionConfig(rotation=2)   # cv2.ROTATE_90_COUNTERCLOCKWISE
    cnone = InspectionConfig(rotation=None)

    assert c90cw.to_dict()["rotation"] == "CLOCKWISE_90",       "0 → CLOCKWISE_90 실패"
    assert c180.to_dict()["rotation"]  == "180",                "1 → 180 실패"
    assert c90cc.to_dict()["rotation"] == "COUNTERCLOCKWISE_90","2 → COUNTERCLOCKWISE_90 실패"
    assert cnone.to_dict()["rotation"] == "NONE",               "None → NONE 실패"
    print("✓ C2 PASS: to_dict() rotation int → 문자열 변환")


def test_c3_from_dict_rotation():
    """from_dict()에서 rotation 문자열이 cv2 int 상수로 역변환되어야 한다."""
    assert InspectionConfig.from_dict({"rotation": "CLOCKWISE_90"}).rotation == 0
    assert InspectionConfig.from_dict({"rotation": "180"}).rotation == 1
    assert InspectionConfig.from_dict({"rotation": "COUNTERCLOCKWISE_90"}).rotation == 2
    assert InspectionConfig.from_dict({"rotation": "NONE"}).rotation is None
    assert InspectionConfig.from_dict({}).rotation is None  # 키 없으면 None
    print("✓ C3 PASS: from_dict() rotation 문자열 → int 역변환")


def test_c4_backward_compat_pulse_count():
    """구버전 reject_pulse_count 필드가 time_valve_on으로 자동 변환되어야 한다."""
    c = InspectionConfig.from_dict({"reject_pulse_count": 3})
    assert c.time_valve_on == pytest_approx(0.3), (
        f"C4 FAIL: reject_pulse_count=3 → time_valve_on={c.time_valve_on} (기대: 0.3)"
    )

    c_null = InspectionConfig.from_dict({"reject_pulse_count": None})
    assert c_null.time_valve_on == pytest_approx(0.1), (
        f"C4 FAIL: reject_pulse_count=None → time_valve_on={c_null.time_valve_on} (기대: 0.1)"
    )
    print("✓ C4 PASS: reject_pulse_count → time_valve_on 자동 변환")


def test_c5_project_name_fallback():
    """project_name이 없거나 비어 있으면 line_name으로 자동 채워져야 한다."""
    c = InspectionConfig.from_dict({"line_name": "line-42"})
    assert c.project_name == "line-42", (
        f"C5 FAIL: project_name={c.project_name!r} (기대: 'line-42')"
    )

    c_empty = InspectionConfig.from_dict({"line_name": "line-99", "project_name": ""})
    assert c_empty.project_name == "line-99", (
        f"C5 FAIL: empty project_name → {c_empty.project_name!r} (기대: 'line-99')"
    )
    print("✓ C5 PASS: project_name 없으면 line_name으로 자동 채움")


def test_c6_products_active_merge():
    """products + active_product 있을 때 활성 제품 필드가 flat에 병합되어야 한다."""
    data = {
        "line_name": "test-line",
        "active_product": "ProductA",
        "products": {
            "ProductA": {
                "reject_delay_frames": 25,
                "reject_mode": "continuous",
                "time_valve_on": 0.3,
                "rotation": "CLOCKWISE_90",
            },
            "ProductB": {
                "reject_delay_frames": 5,
            }
        }
    }
    c = InspectionConfig.from_dict(data)
    assert c.reject_delay_frames == 25,      f"C6 FAIL: reject_delay_frames={c.reject_delay_frames}"
    assert c.reject_mode == "continuous",    f"C6 FAIL: reject_mode={c.reject_mode}"
    assert c.time_valve_on == pytest_approx(0.3), f"C6 FAIL: time_valve_on={c.time_valve_on}"
    assert c.rotation == 0,                  f"C6 FAIL: rotation={c.rotation} (기대: 0=CLOCKWISE_90)"
    assert c.active_product == "ProductA"
    print("✓ C6 PASS: products + active_product → 활성 제품 필드 flat 병합")


def test_c7_unknown_keys_ignored():
    """from_dict()에서 알 수 없는 키가 있어도 TypeError 없이 동작해야 한다."""
    try:
        c = InspectionConfig.from_dict({
            "line_name": "safe-line",
            "unknown_future_field": "some_value",
            "another_unknown": 12345,
        })
        assert c.line_name == "safe-line"
    except TypeError as e:
        assert False, f"C7 FAIL: TypeError 발생 — {e}"
    print("✓ C7 PASS: from_dict() 알 수 없는 키 무시")


def test_c8_to_dict_strips_product_fields_when_products_exist():
    """to_dict()에서 products가 있으면 flat product 필드가 제거되어야 한다."""
    c = InspectionConfig(
        line_name="strip-test",
        active_product="P1",
        products={"P1": {"reject_delay_frames": 5}},
        reject_delay_frames=5,
        reject_mode="individual",
    )
    d = c.to_dict()
    for field in PRODUCT_LEVEL_FIELDS:
        assert field not in d, (
            f"C8 FAIL: 제거되어야 할 product 필드 '{field}'가 to_dict()에 남아 있음"
        )
    # 라인 레벨 필드는 유지
    assert "line_name" in d
    assert "camera_ip" in d
    print("✓ C8 PASS: products 있을 때 to_dict()에서 flat product 필드 제거")


def test_c9_json_roundtrip():
    """to_json() → from_json() 라운드트립 후 핵심 필드가 동일해야 한다."""
    original = InspectionConfig(
        line_name="roundtrip-line",
        project_name="RT Test",
        camera_ip="192.168.5.5",
        reject_delay_frames=15,
        reject_positions=3,
        reject_mode="continuous",
        time_valve_on=0.25,
        rotation=1,  # cv2.ROTATE_180
        class_thresholds={"defect": 0.75, "scratch": 0.60},
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test_config.json")
        original.to_json(path)
        loaded = InspectionConfig.from_json(path)

    assert loaded.line_name          == original.line_name
    assert loaded.project_name       == original.project_name
    assert loaded.camera_ip          == original.camera_ip
    assert loaded.reject_delay_frames == original.reject_delay_frames
    assert loaded.reject_positions   == original.reject_positions
    assert loaded.reject_mode        == original.reject_mode
    assert abs(loaded.time_valve_on - original.time_valve_on) < 1e-9
    assert loaded.rotation           == original.rotation
    assert loaded.class_thresholds   == original.class_thresholds
    print("✓ C9 PASS: to_json() / from_json() 라운드트립")


# ═══════════════════════════════════════════════════════════════════════════════
# backend 유틸 함수 테스트
# ═══════════════════════════════════════════════════════════════════════════════

def _with_settings_path(tmp_path: str):
    """_SETTINGS_PATH를 임시 경로로 바꿔 테스트하는 컨텍스트 매니저."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        original = _main._SETTINGS_PATH
        _main._SETTINGS_PATH = tmp_path
        try:
            yield
        finally:
            _main._SETTINGS_PATH = original

    return _ctx()


def test_g1_load_settings_missing_file():
    """설정 파일이 없으면 기본값이 반환되어야 한다."""
    with tempfile.TemporaryDirectory() as tmp:
        nonexistent = os.path.join(tmp, "no_such_file.json")
        with _with_settings_path(nonexistent):
            settings = _main._load_global_settings()

    assert "storage" in settings
    assert "admin" in settings
    assert settings["storage"]["storage_type"] == "local"
    assert settings["storage"]["local_retention_days"] == 180
    assert settings["admin"]["password"] == "1234"
    print("✓ G1 PASS: 설정 파일 없음 → 기본값 반환")


def test_g2_load_settings_partial_file():
    """부분적으로 채워진 설정 파일을 기본값과 병합해야 한다."""
    partial = {
        "storage": {"save_root": "/custom/path"},
        "admin": {"password": "mypass"}
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "settings.json")
        with open(path, "w") as f:
            json.dump(partial, f)
        with _with_settings_path(path):
            settings = _main._load_global_settings()

    # 커스텀 값 유지
    assert settings["storage"]["save_root"] == "/custom/path"
    assert settings["admin"]["password"] == "mypass"
    # 기본값 병합 확인
    assert settings["storage"]["storage_type"] == "local"
    assert settings["storage"]["local_retention_days"] == 180
    print("✓ G2 PASS: 부분 설정 파일 → 기본값과 병합")


def test_g3_save_load_roundtrip():
    """저장 후 다시 불러오면 동일한 내용이어야 한다."""
    payload = {
        "storage": {
            "save_root": "/tmp/test_imgs",
            "local_retention_days": 90,
            "storage_type": "s3",
            "s3_bucket": "my-bucket",
            "s3_region": "us-west-2",
            "s3_access_key": "AKIAIOSFODNN7EXAMPLE",
            "s3_secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "s3_prefix": "factory/",
            "s3_retention_days": 365,
            "s3_cleanup_interval_hours": 12,
        },
        "admin": {"password": "secure123"}
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "settings.json")
        with _with_settings_path(path):
            _main._save_global_settings(payload)
            loaded = _main._load_global_settings()

    assert loaded["storage"]["s3_bucket"] == "my-bucket"
    assert loaded["storage"]["s3_region"] == "us-west-2"
    assert loaded["storage"]["storage_type"] == "s3"
    assert loaded["admin"]["password"] == "secure123"
    print("✓ G3 PASS: _save_global_settings → _load_global_settings 라운드트립")


def test_g4_mask_secret():
    """시크릿 마스킹: 빈 값 → '****', 짧은 키 → '****', 긴 키 → '****' + 마지막 4자."""
    assert _main._mask_secret("") == "****",                "G4 FAIL: 빈 문자열"
    assert _main._mask_secret("AB") == "****",              "G4 FAIL: 2자 짧은 키"
    assert _main._mask_secret("1234") == "****",            "G4 FAIL: 정확히 4자"
    result = _main._mask_secret("AKIAIOSFODNN7EXAMPLE")
    assert result == "****MPLE",                             f"G4 FAIL: 긴 키 → {result!r}"
    assert result.startswith("****"),                        "G4 FAIL: '****'로 시작하지 않음"
    print("✓ G4 PASS: _mask_secret 마스킹 동작")


def test_g5_migrate_config_no_products():
    """구버전 flat config에 products가 없으면 'Default' 제품이 자동 생성되어야 한다."""
    flat_config = {
        "line_name": "old-line",
        "reject_delay_frames": 8,
        "reject_mode": "individual",
        "time_valve_on": 0.15,
        "model_path": "weights/best.pt",
        "device": "cpu",
    }
    _main._migrate_config(flat_config)

    assert "products" in flat_config,              "G5 FAIL: products 없음"
    assert "Default" in flat_config["products"],   "G5 FAIL: Default 제품 없음"
    assert flat_config["active_product"] == "Default"
    assert flat_config["products"]["Default"]["reject_delay_frames"] == 8
    print("✓ G5 PASS: flat 구버전 config → 'Default' 제품 자동 생성")


def test_g6_migrate_config_with_products():
    """products가 이미 있으면 active_product만 첫 번째로 설정되어야 한다."""
    config = {
        "line_name": "new-line",
        "products": {
            "Alpha": {"reject_delay_frames": 3},
            "Beta":  {"reject_delay_frames": 7},
        }
        # active_product 없음 → 첫 번째("Alpha")로 설정
    }
    _main._migrate_config(config)

    assert config["active_product"] == "Alpha",  (
        f"G6 FAIL: active_product={config['active_product']!r} (기대: 'Alpha')"
    )
    print("✓ G6 PASS: products 있으면 active_product 첫 번째 항목으로 설정")


def test_g7_expand_product_fields():
    """_expand_product_fields()가 active 제품의 필드를 flat 레벨로 복사해야 한다."""
    config = {
        "line_name": "expand-line",
        "active_product": "Pro",
        "products": {
            "Pro": {
                "reject_delay_frames": 20,
                "reject_mode": "continuous",
                "model_path": "weights/pro.pt",
            }
        }
    }
    _main._expand_product_fields(config)

    assert config.get("reject_delay_frames") == 20,      "G7 FAIL: reject_delay_frames"
    assert config.get("reject_mode") == "continuous",    "G7 FAIL: reject_mode"
    assert config.get("model_path") == "weights/pro.pt", "G7 FAIL: model_path"
    # 라인 레벨 필드는 변경 없음
    assert config["line_name"] == "expand-line"
    print("✓ G7 PASS: _expand_product_fields() active 제품 필드 → flat 복사")


def test_g8_verify_password():
    """비밀번호 비교: 일치 → True, 불일치 → False."""
    assert _main._verify_password("secret123", "secret123") is True,  "G8 FAIL: 일치"
    assert _main._verify_password("wrong",     "secret123") is False, "G8 FAIL: 불일치"
    assert _main._verify_password("",          "secret123") is False, "G8 FAIL: 빈 입력"
    assert _main._verify_password("secret123", "") is False,          "G8 FAIL: 빈 저장값"
    print("✓ G8 PASS: _verify_password 일치/불일치")


# ── float 비교 헬퍼 ─────────────────────────────────────────────────────────

class _Approx:
    def __init__(self, expected, tol=1e-9):
        self._e = expected
        self._t = tol

    def __eq__(self, actual):
        return abs(actual - self._e) <= self._t

    def __repr__(self):
        return f"≈{self._e}"


def pytest_approx(value, tol=1e-9):
    return _Approx(value, tol)


# ── 실행 ─────────────────────────────────────────────────────────────────────

TESTS = [
    # InspectionConfig
    test_c1_defaults,
    test_c2_to_dict_rotation,
    test_c3_from_dict_rotation,
    test_c4_backward_compat_pulse_count,
    test_c5_project_name_fallback,
    test_c6_products_active_merge,
    test_c7_unknown_keys_ignored,
    test_c8_to_dict_strips_product_fields_when_products_exist,
    test_c9_json_roundtrip,
    # backend helpers
    test_g1_load_settings_missing_file,
    test_g2_load_settings_partial_file,
    test_g3_save_load_roundtrip,
    test_g4_mask_secret,
    test_g5_migrate_config_no_products,
    test_g6_migrate_config_with_products,
    test_g7_expand_product_fields,
    test_g8_verify_password,
]

if __name__ == "__main__":
    import traceback

    passed = 0
    failed = 0

    print("=" * 60)
    print("InspectionConfig + backend 설정 검증 테스트")
    print("=" * 60)

    for t in TESTS:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
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
