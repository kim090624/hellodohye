# -*- coding: utf-8 -*-
"""
야생동물 실시간 감지 시스템 | 메인 오케스트레이터 (Main Orchestrator)
보고서의 제 2장, 3장, 4장을 통합하여 실행하는 마스터 스크립트입니다.
기존 test.py를 대체하며, 단원별로 쪼개진 모듈들을 조립해 실시간 파이프라인을 가동합니다.
"""
import sys
import io
# Windows 터미널 cp949 환경에서 이모지/한글 출력 시 UnicodeEncodeError 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf_8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import serial
import time
import numpy as np
import matplotlib.pyplot as plt
from collections import deque
import config

# 각 장(Chapter)별 핵심 모듈 불러오기
from step_analyzer import StepDurationAnalyzer
from sdft_filter import SDFTAdaptiveFilter
from msr_engine import BistableDoubleWellEngine
from acf_analyzer import ACFPeriodicityAnalyzer
from ui_manager import WildlifeUI

import os
import glob
import csv
import time as _time_module

# 파일 로드 시 추적을 위한 변수
LAST_LOADED_ANIMAL_FILE = "알 수 없음"
LAST_LOADED_NOISE_FILE = "알 수 없음"


def parse_serial_line(ln, channel):
    """아두이노가 보낸 시리얼 데이터(단일값 또는 쉼표 구분값)에서 지정된 채널 값을 추출."""
    if ',' in ln:
        parts = ln.split(',')
        if len(parts) > channel:
            try: return int(parts[channel])
            except ValueError: pass
        try: return int(parts[0])
        except ValueError: return None
    try: return int(ln)
    except ValueError: return None


def auto_calibrate_zero(ser, channel):
    """2초간 센서 신호를 읽어 실제 영점(DC Bias)을 자동으로 계산합니다."""
    print(f"\n⏳ 센서 영점(Baseline) 자동 보정 중... (아두이노 채널 {channel}번)")
    samples = []
    start = _time_module.time()
    ser.reset_input_buffer()
    while _time_module.time() - start < 2.0:
        if ser.in_waiting > 0:
            ln = ser.readline().decode('utf-8', errors='ignore').strip()
            if ln:
                val = parse_serial_line(ln, channel)
                if val is not None:
                    samples.append(val)
        _time_module.sleep(0.001)
    if len(samples) > 20:
        zero = float(np.mean(samples))
        print(f"✅ 영점 보정 완료: {zero:.2f} (기본에서 최적화됨)")
        return zero
    
    # 캘리브레이션 실패 시 센서 채널별 물리적 기본 바이어스로 안전하게 후퇴(Fallback)
    default_zero = 250.0 if channel == 1 else 512.0
    print(f"⚠️ 영점 보정 실패 (데이터 부족) — 센서 채널 {channel}번 기본값 {default_zero:.1f}을 사용합니다.")
    return default_zero


# 센서 종류 → 저장 폴더 매핑
SENSOR_FOLDERS = {
    '1': ('Geophone',          'geophone'),
    '2': ('surround noise',    'surround_noise'),
}


def _pick_file_dialog(title, initialdir, filetypes, parent=None):
    """Tkinter 파일 선택 다이얼로그를 열고 선택된 경로를 반환합니다. topmost 속성을 사용해 무조건 화면 최상단에 팝업시킵니다."""
    import tkinter as tk
    from tkinter import filedialog
    root_tmp = None
    try:
        if parent is None:
            root_tmp = tk.Tk()
            root_tmp.withdraw()
            root_tmp.attributes("-topmost", True)
            chosen = filedialog.askopenfilename(
                title=title, initialdir=initialdir, filetypes=filetypes, parent=root_tmp)
        else:
            # 다이얼로그가 열려 있는 동안 부모 창을 최상단으로 올림
            parent.attributes("-topmost", True)
            chosen = filedialog.askopenfilename(
                title=title, initialdir=initialdir, filetypes=filetypes, parent=parent)
            parent.attributes("-topmost", False) # 완료 후 원복
    finally:
        if root_tmp is not None:
            root_tmp.destroy()
    return chosen if chosen else None


def load_animal_data_from_file(parent=None):
    """파일 탐색기로 지오폰 CSV를 선택하고 로드합니다."""
    folder_geo = os.path.abspath('Geophone')
    os.makedirs(folder_geo, exist_ok=True)

    print("\n📂 [지오폰 발걸음 CSV] 파일 선택 창을 열고 있습니다...")
    chosen_geo = _pick_file_dialog(
        title='[지오폰] 발걸음 CSV 파일을 선택하세요',
        initialdir=folder_geo,
        filetypes=[('CSV 파일', '*.csv'), ('모든 파일', '*.*')],
        parent=parent
    )
    if not chosen_geo:
        print("⚠️ 취소되었습니다.")
        return None

    print(f" ✅ 지오폰 파일 선택: {os.path.basename(chosen_geo)}")

    # 지오폰 로드
    raw = []
    with open(chosen_geo, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            try: raw.append(int(row['raw_value']))
            except: pass
            if i % 1000 == 0 and parent is not None:
                parent.update()

    if len(raw) < 100:
        print("❌ 파일에 데이터가 너무 적습니다 (최소 100샘플 필요).")
        return None

    global LAST_LOADED_ANIMAL_FILE
    LAST_LOADED_ANIMAL_FILE = os.path.basename(chosen_geo)
    raw_arr = np.array(raw, dtype=float)
    # 파일에서 로드할 때는 파일 자체의 평균값으로 DC Bias를 제거합니다.
    # (실시간 모드에서는 auto_calibrate_zero()로 얻은 config.CURRENT_ZERO를 사용하지만,
    #  파일 로드 시에는 센서가 없으므로 데이터 평균을 영점으로 삼습니다.)
    file_zero = np.mean(raw_arr)
    centered_geo = raw_arr - file_zero
    print(f"✅ 파일 로드 성공: {os.path.basename(chosen_geo)} ({len(centered_geo)} 샘플, 파일 영점={file_zero:.1f})")
    return centered_geo


def load_env_noise_from_file(parent=None):
    """파일 탐색기로 배경 소음 파일(.npy 또는 .csv)을 선택하여 로드합니다."""
    init_dir = os.path.abspath('surround noise') if os.path.exists('surround noise') else os.path.abspath('.')

    print("\n📂 [지오폰 배경 소음] 파일 선택 창을 열고 있습니다 (npy 또는 csv)...")
    chosen_geo = _pick_file_dialog(
        title='[지오폰] 배경 소음 파일을 선택하세요 (.npy 또는 .csv)',
        initialdir=init_dir,
        filetypes=[
            ('NumPy 배열 (npy)', '*.npy'),
            ('CSV 데이터', '*.csv'),
            ('모든 파일', '*.*')
        ],
        parent=parent
    )
    if not chosen_geo:
        print("⚠️ 지오폰 배경 소음 파일 선택이 취소되었습니다.")
        return None
    global LAST_LOADED_NOISE_FILE
    LAST_LOADED_NOISE_FILE = os.path.basename(chosen_geo)
    print(f" 지오폰 소음 파일 선택: {os.path.basename(chosen_geo)}")

    def _load_noise_file(path):
        """확장자에 따라 npy 또는 csv를 로드하고 DC 성분을 제거한 배열을 반환."""
        ext = os.path.splitext(path)[1].lower()
        if ext == '.npy':
            arr = np.load(path)
        elif ext == '.csv':
            import csv as _csv
            raw = []
            with open(path, newline='', encoding='utf-8-sig') as f:
                reader = _csv.DictReader(f)
                col_candidates = ['raw_value', 'geophone_raw']
                for i, row in enumerate(reader):
                    for col in col_candidates:
                        if col in row:
                            try: raw.append(int(row[col])); break
                            except: pass
                    if i % 1000 == 0 and parent is not None:
                        parent.update()
            if len(raw) < 100:
                return None
            arr = np.array(raw, dtype=float)
        else:
            print(f"지원하지 않는 파일 형식입니다: {ext}")
            return None
        return arr - np.mean(arr)   # DC Bias 제거

    env_geo = _load_noise_file(chosen_geo)

    if env_geo is None:
        print("파일 로드에 실패했습니다. 데이터가 너무 적거나 형식이 맞지 않습니다.")
        return None

    print(f"배경 소음 로드 완료! 지오폰 {len(env_geo)} 샘플")
    return env_geo


def _optimize_from_data(animal_signal_centered, env_noise_centered, ui=None, use_sdft=False):
    """
    SDFT 필터 학습 -> 발걸음 추출 -> a, b, sigma 최적화 연산.
    이 함수는 백그라운드 스레드에서 호출됩니다.
    tkinter 호출(root.update 등)은 절대 하지 마세요.
    UI 업데이트는 모든 연산이 끝난 이후 메인 스레드에서만 처리합니다.
    """
    buffer_size = config.BUFFER_SIZE
    calib_sdft = SDFTAdaptiveFilter()

    if use_sdft:
        print("\n[OPT] 최적화 연산 가동 중 (SDFT 필터 연동)...")
        print(" [OPT] SDFT 필터에 배경 소음 학습 중...")
        for idx in range(0, len(env_noise_centered) - buffer_size, buffer_size):
            chunk = env_noise_centered[idx : idx + buffer_size]
            calib_sdft.process(chunk)

        print(" [OPT] SDFT 기반 발걸음(Transient) 조각 정밀 스캔 중...")
        valid_footstep_chunks = []
        if len(animal_signal_centered) > buffer_size:
            for idx in range(0, len(animal_signal_centered) - buffer_size, int(buffer_size/2)):
                chunk = animal_signal_centered[idx : idx + buffer_size]
                filtered_chunk, _, _, transient_detected = calib_sdft.process(chunk)
                if transient_detected:
                    valid_footstep_chunks.append(filtered_chunk)

        if valid_footstep_chunks:
            best_chunk = max(valid_footstep_chunks, key=lambda c: np.sum(np.abs(c)))
            combined_input = best_chunk
            print(f"[OPT] SDFT가 발걸음으로 판정한 {len(valid_footstep_chunks)}개 구간 중 최상위 조각 추출 완료!")
        else:
            print("[OPT] SDFT 감지 실패 — 진폭 기반으로 우회 추출합니다.")
            max_sum, best_idx = -1, 0
            for idx in range(0, len(animal_signal_centered) - buffer_size, 10):
                s = np.sum(np.abs(animal_signal_centered[idx : idx + buffer_size]))
                if s > max_sum:
                    max_sum, best_idx = s, idx
            raw_chunk = animal_signal_centered[best_idx : best_idx + buffer_size]
            combined_input, _, _, _ = calib_sdft.process(raw_chunk)
    else:
        print("\n[OPT] 최적화 연산 가동 중 (SDFT 비적용 순수 원시 데이터 분석)...")
        max_sum, best_idx = -1, 0
        
        # 데이터가 너무 짧아서 루프가 안 도는 경우 방지
        search_end = max(1, len(animal_signal_centered) - buffer_size + 1)
        for idx in range(0, search_end, 10):
            s = np.sum(np.abs(animal_signal_centered[idx : idx + buffer_size]))
            if s > max_sum:
                max_sum, best_idx = s, idx
        combined_input = animal_signal_centered[best_idx : best_idx + buffer_size]
        print(f"[OPT] 진폭 기반 원시 신호 발걸음 조각 추출 완료! (인덱스: {best_idx})")

    # combined_input이 buffer_size보다 짧은 경우 (예: 데이터 자체가 300샘플 미만)
    # 남은 공간을 0으로 패딩하여 형태(shape)를 맞춤으로써 연산 중 ValueError를 방지합니다.
    if len(combined_input) < buffer_size:
        combined_input = np.pad(combined_input, (0, buffer_size - len(combined_input)), mode='constant')
    # Phase 1: a 파라미터 찾기
    # a 범위: 0.001 ~ 0.1, 0.001 간격 (100개). b는 1로 고정.
    a_range = np.round(np.arange(0.001, 0.101, 0.001), 3)
    engine = BistableDoubleWellEngine()
    print(f" [OPT] Phase 1: a 탐색 중 (0.001~0.100, {len(a_range)}회)...")
    
    min_crossings = float('inf')
    best_a = a_range[-1] # 기본값

    # 가장 낮은 N_t (최소화되는 임계값)를 찾고, 동일하게 낮은 N_t를 가지는 a 중에서 가장 작은 a (가장 반응 속도가 빠른 낮은 역치)를 선택
    for a_val in a_range:
        engine.a = a_val
        engine.b = 1.0  # b는 1로 고정
        engine.reset_states()
        
        # 소음 없이 데이터만 주입 (K_t는 무시)
        _, _, N_t, _ = engine.process_buffer(combined_input, np.zeros_like(combined_input))
        
        # N_t가 현재 최소 crossing보다 작으면 갱신 (a_range가 오름차순이므로 자연스레 가장 작은 a가 선택됨)
        if N_t < min_crossings:
            min_crossings = N_t
            best_a = a_val

    opt_a = best_a
    opt_b = 1.0
    print(f" [OPT] Phase 1 완료: opt_a = {opt_a:.2f}, opt_b = {opt_b:.1f} (데이터 단독 전이 횟수: {min_crossings}회)")

    # Phase 2: sigma (소음 강도) 파라미터 찾기
    VOLT_TO_ADC = 204.8
    sigma_v_range = np.linspace(0.02, 0.30, 15)
    sigma_adc_range = sigma_v_range * VOLT_TO_ADC
    
    engine.a = opt_a
    engine.b = opt_b
    
    rng = np.random.default_rng(seed=42)
    print(f" [OPT] Phase 2: sigma 탐색 중 (0.02V~0.30V, {len(sigma_adc_range)}회)...")
    
    max_net_events = -1
    best_sigma_idx = 0

    for i, sigma in enumerate(sigma_adc_range):
        white_noise = rng.normal(0, sigma, buffer_size)
        engine.reset_states()
        # 신호+소음(N_t) 및 소음단독(K_t) 측정
        _, _, N_t, K_t = engine.process_buffer(combined_input, white_noise)
        net_events = N_t - K_t
        
        # 순수 확률 공명 효과(N_t - K_t)가 가장 극대화되는 지점 탐색
        if net_events > max_net_events:
            max_net_events = net_events
            best_sigma_idx = i

    opt_sigma_adc = sigma_adc_range[best_sigma_idx]
    print(f" [OPT] Phase 2 완료: opt_sigma = {sigma_v_range[best_sigma_idx]:.2f}V ({opt_sigma_adc:.1f} ADC)")

    print("\n" + "="*60)
    print("[OPT] [자동 파라미터 튜닝 완료] 최적화 결과")
    print(f" >> 역치 파라미터 (a, b) : {opt_a:.3f}")
    print(f" >> 최적 인공 소음 (sigma) : {sigma_v_range[best_sigma_idx]:.2f} V ({opt_sigma_adc:.1f} ADC)")
    print("="*60)

    # UI 업데이트용 데이터 계산 (스레드 내 순수 연산만, tkinter 호출 없음)
    engine.reset_states()
    white_noise = rng.normal(0, opt_sigma_adc, buffer_size)
    x_arr_tot, x_arr_noi, N_t, K_t = engine.process_buffer(combined_input, white_noise)
    telegraph_signal = np.sign(x_arr_tot)

    acf_analyzer = ACFPeriodicityAnalyzer()
    acf_r, cadence = acf_analyzer.compute(telegraph_signal)

    half_n = buffer_size // 2
    raw_chunk = (animal_signal_centered[:buffer_size]
                 if len(animal_signal_centered) >= buffer_size
                 else np.pad(animal_signal_centered, (0, buffer_size - len(animal_signal_centered))))
    fft_raw   = np.abs(np.fft.fft(raw_chunk))[:half_n]
    fft_clean = np.abs(np.fft.fft(combined_input))[:half_n]
    fft_avg   = (np.abs(np.fft.fft(env_noise_centered[:buffer_size]))[:half_n]
                 if len(env_noise_centered) >= buffer_size else np.zeros(half_n))

    tracked_peak = (calib_sdft.detected_noise_bands[0][0] + config.NOISE_BAND_WIDTH/2
                    if calib_sdft.detected_noise_bands else 0.0)
    noise_bands = calib_sdft.detected_noise_bands

    # 스레드로부터 반환할 결과 딕셔너리
    return {
        'opt_a':        opt_a,
        'opt_b':        opt_b,
        'opt_sigma':    opt_sigma_adc,
        'sigma_v':      sigma_v_range[best_sigma_idx],
        'raw_chunk':    raw_chunk,
        'combined':     combined_input,
        'telegraph':    telegraph_signal,
        'N_t':          N_t,
        'K_t':          K_t,
        'net_events':   max(0, N_t - K_t),
        'acf_r':        acf_r,
        'cadence':      cadence,
        'fft_raw':      fft_raw,
        'fft_clean':    fft_clean,
        'fft_avg':      fft_avg,
        'tracked_peak': tracked_peak,
        'noise_bands':  noise_bands,
    }


def _run_env_noise_ui(ser, ui):
    """배경 소음 수집을 메인 스레드에서 실행하며 UI를 업데이트."""
    # 영점 자동 조절 (싱글 채널)
    zero_geo = auto_calibrate_zero(ser, 0)
    config.CURRENT_ZERO = zero_geo

    ui.show_recording_graph(
        '📡 [1단계] 배경 소음 측정',
        '센서 주변을 조용히 유지해 주세요 — 30분 동안 수집합니다')
    samples = []
    start = _time_module.time()
    last_print = 0.0
    while _time_module.time() - start < 1800.0:  # 30분(1800초) 동안 수집
        if not ui.is_alive(): 
            break  # 창을 닫아도 수집된 곳까지만이라도 무조건 저장하도록 변경
        while ser.in_waiting > 0:
            ln = ser.readline().decode('utf-8', errors='ignore').strip()
            if ln:
                val = parse_serial_line(ln, 0)
                if val is not None:
                    samples.append(val)
        elapsed = _time_module.time() - start
        if elapsed - last_print >= 0.05:
            pct = elapsed / 1800.0 * 100
            ui.update_recording_graph(
                samples,
                f'⏳ 수집 중... {pct:.1f}%  ({len(samples)} 샘플)',
                '#74b9ff')
            last_print = elapsed
        ui.root.update()
        _time_module.sleep(0.001)
    if len(samples) < 100:
        ui.update_recording_graph(samples, '❌ 데이터 부족 — 아두이노 연결을 확인하세요', '#d63031')
        _time_module.sleep(2.0)
        return
    
    # 데이터 처리 및 저장
    env_geo = np.array(samples) - config.CURRENT_ZERO
    
    # 1. 루트 폴더에 기준선 저장
    np.save('env_noise_baseline.npy', env_geo)
    
    # 2. 'surround noise' 폴더에 CSV 백업 저장
    folder_name = 'surround noise'
    os.makedirs(folder_name, exist_ok=True)
    ts = int(_time_module.time())
    
    # CSV 저장
    import csv as _csv
    csv_fname = os.path.join(folder_name, f'noise_geophone_{ts}.csv')
    with open(csv_fname, 'w', newline='') as f:
        w = _csv.writer(f)
        w.writerow(['sample_index', 'raw_value'])
        for i, v in enumerate(samples):
            w.writerow([i, v])
            
    # NPY 저장 백업
    np.save(os.path.join(folder_name, 'env_noise_baseline.npy'), env_geo)
    
    ui.update_recording_graph(samples, f'✅ 완료! {len(samples)} 샘플 → {csv_fname} 저장됨', '#55efc4')
    ui.root.update()
    _time_module.sleep(2.0)


def _run_footstep_ui(ser, ui, env_noise_geo):
    """발걸음 30초 수집 + 최적화를 메인 스레드에서 실행 (싱글 채널)."""
    import threading
    # 영점 자동 조절
    zero_geo = auto_calibrate_zero(ser, 0)
    config.CURRENT_ZERO = zero_geo

    ui.show_recording_graph(
        '👣 [2단계] 발걸음 실측 수집',
        '센서 주변을 힘껏 밟아주세요! (30초)')
    samples = []
    start = _time_module.time()
    last_print = 0.0
    while _time_module.time() - start < 30.0:
        if not ui.is_alive(): return None, None, None
        while ser.in_waiting > 0:
            ln = ser.readline().decode('utf-8', errors='ignore').strip()
            if ln:
                val = parse_serial_line(ln, 0)
                if val is not None:
                    samples.append(val)
        elapsed = _time_module.time() - start
        if elapsed - last_print >= 0.05:
            pct = elapsed / 30.0 * 100
            ui.update_recording_graph(
                samples,
                f'발걸음 수집 중... {pct:.1f}%  ({len(samples)} 샘플)',
                color='#fdcb6e')
            last_print = elapsed
        ui.root.update()
        _time_module.sleep(0.001)
    if len(samples) < 100:
        ui.update_recording_graph(samples, '데이터 부족', color='#d63031')
        ui.root.update()
        _time_module.sleep(2.0)
        return None, None, None
        
    # 'Geophone' 폴더에 CSV 저장
    folder_name = 'Geophone'
    os.makedirs(folder_name, exist_ok=True)
    ts = int(_time_module.time())
    csv_fname = os.path.join(folder_name, f'footstep_geophone_{ts}.csv')
    import csv as _csv
    with open(csv_fname, 'w', newline='') as f:
        w = _csv.writer(f)
        w.writerow(['sample_index', 'raw_value'])
        for i, v in enumerate(samples):
            w.writerow([i, v])
    print(f"발걸음 데이터 저장 완료: {csv_fname}")

    animal_geo = np.array(samples) - config.CURRENT_ZERO

    ui.show_optimization_graph(env_noise_geo, animal_signal=animal_geo)
    ui.update_optimization_graph('최적화 연산 중 (백그라운드)...', raw_signal=animal_geo, color='#a29bfe')
    ui.root.update()

    # ── 백그라운드 스레드에서 최적화 실행 ──────────────────
    result_holder = [None]
    done_event = threading.Event()

    def _worker():
        try:
            result_holder[0] = _optimize_from_data(animal_geo, env_noise_geo, ui=None)
        except Exception as e:
            print(f"최적화 연산 중 오류: {e}")
        finally:
            done_event.set()

    threading.Thread(target=_worker, daemon=True).start()

    # 메인 스레드: UI 갱신하면서 완료 대기
    while not done_event.is_set():
        if not ui.is_alive():
            return None, None, None
        ui.root.update()
        _time_module.sleep(0.02)

    r = result_holder[0]
    if r is None:
        return None, None, None

    # 결과를 메인 스레드에서 UI에 반영 (tkinter는 메인 스레드에서만!)
    ui.update_optimization_graph(
        status=f'최적화 완료! a={r["opt_a"]:.3f}, sigma={r["sigma_v"]:.2f}V ({r["opt_sigma"]:.1f} ADC)',
        raw_signal=r['raw_chunk'], filtered_signal=r['combined'],
        sr_signal=r['telegraph'], N_t=r['N_t'], K_t=r['K_t'],
        net_events=r['net_events'], acf_r=r['acf_r'], cadence=r['cadence'],
        raw_fft=r['fft_raw'], clean_fft=r['fft_clean'], avg_fft=r['fft_avg'],
        tracked_peak=r['tracked_peak'], noise_bands=r['noise_bands'], color='#55efc4')
    ui.root.update()

    # 1.5초 넌블로킹 대기
    t_end = _time_module.time() + 1.5
    while _time_module.time() < t_end:
        ui.root.update(); _time_module.sleep(0.02)

    return r['opt_a'], r['opt_b'], r['opt_sigma']


def _run_file_optimize_ui(animal_geo, env_noise_geo, ui):
    """
    파일 불러오기 최적화.
    최적화 연산은 백그라운드 스레드에서 실행하고,
    UI 업데이트는 메인 스레드(root.after 또는 직접 호출)에서만 수행합니다.
    """
    import threading

    ui.show_optimization_graph(env_noise_geo, animal_signal=animal_geo)
    ui.update_optimization_graph('최적화 연산 중 (백그라운드)...', raw_signal=animal_geo, color='#a29bfe')
    ui.root.update()

    # ── 백그라운드 스레드에서 최적화 실행 ──────────────────
    result_holder = [None]
    done_event = threading.Event()

    def _worker():
        try:
            result_holder[0] = _optimize_from_data(animal_geo, env_noise_geo, ui=None)
        except Exception as e:
            print(f"최적화 연산 중 오류: {e}")
        finally:
            done_event.set()

    threading.Thread(target=_worker, daemon=True).start()

    # 메인 스레드: UI 갱신하면서 완료 대기 (응답없음 완전 방지)
    while not done_event.is_set():
        if not ui.is_alive():
            return None
        ui.root.update()
        _time_module.sleep(0.02)

    r = result_holder[0]
    if r is None:
        return None

    # 결과를 메인 스레드에서 UI에 반영 (tkinter는 메인 스레드에서만!)
    ui.update_optimization_graph(
        status=f'최적화 완료! a={r["opt_a"]:.3f}, sigma={r["sigma_v"]:.2f}V ({r["opt_sigma"]:.1f} ADC)',
        raw_signal=r['raw_chunk'], filtered_signal=r['combined'],
        sr_signal=r['telegraph'], N_t=r['N_t'], K_t=r['K_t'],
        net_events=r['net_events'], acf_r=r['acf_r'], cadence=r['cadence'],
        raw_fft=r['fft_raw'], clean_fft=r['fft_clean'], avg_fft=r['fft_avg'],
        tracked_peak=r['tracked_peak'], noise_bands=r['noise_bands'], color='#55efc4')
    ui.root.update()

    # 1.5초 넌블로킹 대기
    t_end = _time_module.time() + 1.5
    while _time_module.time() < t_end:
        ui.root.update(); _time_module.sleep(0.02)

    return r['opt_a'], r['opt_b'], r['opt_sigma']


def _run_record_ui(ser, ui):
    """신호 녹화를 메인 스레드에서 실행."""
    from ui_manager import SENSOR_FOLDERS
    sel = ui._pending_record_sel
    
    # 영점 자동 조절
    config.CURRENT_ZERO = auto_calibrate_zero(ser, 0)
    
    folder_name, prefix = SENSOR_FOLDERS[sel]
    os.makedirs(folder_name, exist_ok=True)
    label  = ui._pending_record_label
    dur    = ui._pending_record_dur
    ts     = int(_time_module.time())
    fname  = os.path.join(folder_name, f'{prefix}_{label}_{ts}.csv')

    ui.show_recording_graph(
        f'🔴 신호 녹화 — {folder_name}',
        f'{dur:.0f}초 동안 녹화합니다 → {fname}')

    samples = []
    start = _time_module.time()
    last_print = 0.0
    while _time_module.time() - start < dur:
        if not ui.is_alive(): return
        while ser.in_waiting > 0:
            ln = ser.readline().decode('utf-8', errors='ignore').strip()
            if ln:
                val = parse_serial_line(ln, 0)
                if val is not None:
                    samples.append(val)
        elapsed = _time_module.time() - start
        if elapsed - last_print >= 0.05:
            pct = elapsed / dur * 100
            ui.update_recording_graph(samples, f'⏳ {pct:.0f}%  ({len(samples)} 샘플)', '#e17055')
            last_print = elapsed
        ui.root.update()
        _time_module.sleep(0.001)

    import csv as _csv
    with open(fname, 'w', newline='') as f:
        w = _csv.writer(f)
        w.writerow(['sample_index', 'raw_value'])
        for i, v in enumerate(samples):
            w.writerow([i, v])
    ui.update_recording_graph(samples, f'✅ 저장 완료! {len(samples)} 샘플 → {fname}', '#55efc4')
    ui.root.update()
    _time_module.sleep(2.0)




def main():
    # 하드웨어 설정 및 시리얼 연결
    try:
        ser = serial.Serial(config.PORT, config.BAUD_RATE, timeout=0.05)
        print(f'[{config.PORT}] 센서 연결 성공!')
    except Exception as e:
        print(f'포트 연결 실패: {e}')
        return

    # 기본 파라미터 (지오폰)
    opt_a_geo     = config.POTENTIAL_A
    opt_b_geo     = config.POTENTIAL_B
    opt_sigma_geo = config.SIGMA_NOISE

    # Tkinter UI 생성 (메인 스레드)
    ui = WildlifeUI()

    # ── 메뉴 루프 ──────────────────────────────────
    while True:
        if not ui.is_alive():
            break

        choice = ui.wait_for_choice()
        if choice is None:   # Quit 버튼
            break

        if choice == '1':
            _run_env_noise_ui(ser, ui)

        elif choice == '2':
            # 파일 선택창으로 지오폰 배경 소음 로드
            env_noise_geo = load_env_noise_from_file(parent=ui.root)

            if env_noise_geo is None:
                print("⚠️ 배경 소음 파일 선택이 취소되었거나 로드에 실패했습니다.")
                continue
                
            res = _run_footstep_ui(ser, ui, env_noise_geo)
            if res is not None:
                opt_a_geo, opt_b_geo, opt_sigma_geo = res

        elif choice == '3':
            t_start = _time_module.time()
            
            print("\n📂 [1/5] 지오폰 발걸음 파일 선택 중...")
            t_file1_start = _time_module.time()
            animal_geo = load_animal_data_from_file(parent=ui.root)
            if animal_geo is None:
                print("⚠️ 발걸음 파일 로드가 취소되었습니다.")
                continue
            print(f"⏱️ 발걸음 파일 로드 완료: {animal_geo.shape[0]} 샘플 ({_time_module.time() - t_file1_start:.4f}초 소요)")

            print("\n📂 [2/5] 배경 소음 파일 선택 중...")
            t_file2_start = _time_module.time()
            env_noise_geo = load_env_noise_from_file(parent=ui.root)
            if env_noise_geo is None:
                print("⚠️ 배경 소음 파일 로드가 취소되었습니다.")
                continue
            print(f"⏱️ 배경 소음 파일 로드 완료: {env_noise_geo.shape[0]} 샘플 ({_time_module.time() - t_file2_start:.4f}초 소요)")
                
            print("\n⏳ [3/5] 최적 파라미터 수치 해석 최적화 연산 중...")
            t_opt_start = _time_module.time()
            res = _run_file_optimize_ui(animal_geo, env_noise_geo, ui)
            opt_duration = _time_module.time() - t_opt_start
            print(f"⏱️ 최적화 연산 완료 ({opt_duration:.4f}초 소요)")
            
            if res is not None:
                t_save_start = _time_module.time()
                opt_a_geo, opt_b_geo, opt_sigma_geo = res
                
                # real.data 폴더 생성 및 최적화 결과 저장
                save_dir = 'real.data'
                os.makedirs(save_dir, exist_ok=True)
                
                timestamp_str = time.strftime('%Y%m%d_%H%M%S')
                log_filename = os.path.join(save_dir, f'parameters_{timestamp_str}.csv')
                
                # 1. 개별 실행 파일로 저장
                with open(log_filename, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(['timestamp', 'animal_file', 'noise_file', 'opt_a', 'opt_b', 'opt_sigma'])
                    writer.writerow([timestamp_str, LAST_LOADED_ANIMAL_FILE, LAST_LOADED_NOISE_FILE, opt_a_geo, opt_b_geo, opt_sigma_geo])
                
                # 2. 누적 히스토리 파일에 추가 기록
                summary_filename = os.path.join(save_dir, 'optimization_history.csv')
                file_exists = os.path.exists(summary_filename)
                with open(summary_filename, 'a', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(['timestamp', 'animal_file', 'noise_file', 'opt_a', 'opt_b', 'opt_sigma'])
                    writer.writerow([timestamp_str, LAST_LOADED_ANIMAL_FILE, LAST_LOADED_NOISE_FILE, opt_a_geo, opt_b_geo, opt_sigma_geo])
                
                print(f"⏱️ 데이터 파일 저장 완료 ({_time_module.time() - t_save_start:.4f}초 소요)")
                print(f"\n📊 [real.data] 최적 파라미터 저장 완료: a={opt_a_geo:.3f}, b={opt_b_geo:.1f}, sigma={opt_sigma_geo:.2f}")
                print(f"   └─ 개별 기록: {log_filename}")
                print(f"   └─ 누적 기록: {summary_filename}")
                print(f"⏱️ [총 소요 시간] 3번 기능 전체 완료까지 총 {_time_module.time() - t_start:.4f}초가 걸렸습니다.")

        elif choice == '4':
            print('\n📡 지오폰 채널 녹화를 진행합니다.')
            label = input('👉 레이블 입력 (기본=raw): ').strip() or 'raw'
            dur_s = input('👉 녹화 시간 입력 (초, 기본=30): ').strip()
            try:    dur = float(dur_s) if dur_s else 30.0
            except: dur = 30.0
            ui._pending_record_sel   = '1'
            ui._pending_record_label = label
            ui._pending_record_dur   = dur
            _run_record_ui(ser, ui)

        elif choice == '5' or choice == '6':
            break   # 실시간 감지 또는 비교 모드로 진입

    if not ui.is_alive():
        ser.close()
        return

    # ── 파이프라인 컴포넌트 (공통) ───────────────────────────
    # 지오폰 채널 영점 보정
    zero_geo = auto_calibrate_zero(ser, 0)
    config.CURRENT_ZERO = zero_geo

    data_buffer_geo   = deque(maxlen=config.BUFFER_SIZE)
    raw_val_buffer_geo = deque(maxlen=config.BUFFER_SIZE)
    sdft_filter_geo   = SDFTAdaptiveFilter()
    
    _dc_ema_geo = [None]
    _DC_ALPHA   = 0.002   # 매우 느린 EMA → 0.2% 갱신 = ~500샘플 시정수 (≈5초 @ 100Hz)
    
    last_render_time = time.time()
    last_detection_time = 0.0

    if choice == '6':
        # ── 실시간 비교 모드 (Mode 6) ───────────────────────────
        opt_a_geo1 = config.POTENTIAL_A
        opt_b_geo1 = config.POTENTIAL_B
        opt_sigma_geo1 = config.SIGMA_NOISE
        
        opt_a_geo2 = 0.083
        opt_b_geo2 = 1.0
        opt_sigma_geo2 = 13.98
        
        bistable_engine1 = BistableDoubleWellEngine()
        bistable_engine1.a = opt_a_geo1
        bistable_engine1.b = opt_b_geo1
        
        bistable_engine2 = BistableDoubleWellEngine()
        bistable_engine2.a = opt_a_geo2
        bistable_engine2.b = opt_b_geo2
        
        acf_analyzer1 = ACFPeriodicityAnalyzer()
        acf_analyzer2 = ACFPeriodicityAnalyzer()
        
        ui.setup_comparison_detection()
        print('시스템 엔진 가동 중 (Mode 6: SR 비교 분석)...')
        
        try:
            while ui.is_alive():
                data_updated = False
                while ser.in_waiting > 0:
                    ln = ser.readline().decode('utf-8', errors='ignore').strip()
                    if ln:
                        val = parse_serial_line(ln, 0)
                        if val is not None:
                            raw_centered = float(val) - zero_geo
                            if _dc_ema_geo[0] is None: _dc_ema_geo[0] = raw_centered
                            else: _dc_ema_geo[0] = (1.0 - _DC_ALPHA) * _dc_ema_geo[0] + _DC_ALPHA * raw_centered
                            data_buffer_geo.append(raw_centered - _dc_ema_geo[0])
                            raw_val_buffer_geo.append(float(val))
                            data_updated = True

                current_time = time.time()
                if data_updated and len(data_buffer_geo) >= config.BUFFER_SIZE and (current_time - last_render_time >= config.RENDER_INTERVAL):
                    signal_geo = np.array(data_buffer_geo)
                    signal_geo = signal_geo * (1.0 - np.exp(-np.abs(signal_geo) / 2.5))
                    
                    filtered_signal_geo, _, _, _ = sdft_filter_geo.process(signal_geo)
                    
                    wn1 = np.random.normal(0, opt_sigma_geo1, len(filtered_signal_geo))
                    x_tot1, _, n1, k1 = bistable_engine1.process_buffer(filtered_signal_geo, wn1)
                    nk1 = max(0, n1 - k1)
                    r1, _ = acf_analyzer1.compute(np.sign(x_tot1))
                    
                    wn2 = np.random.normal(0, opt_sigma_geo2, len(filtered_signal_geo))
                    x_tot2, _, n2, k2 = bistable_engine2.process_buffer(filtered_signal_geo, wn2)
                    nk2 = max(0, n2 - k2)
                    r2, _ = acf_analyzer2.compute(np.sign(x_tot2))
                    
                    ui.update_comparison(
                        np.array(raw_val_buffer_geo),
                        np.sign(x_tot1), n1, k1, nk1, r1,
                        np.sign(x_tot2), n2, k2, nk2, r2
                    )
                    
                    is_wildlife_confirmed = (r2 >= 0.6)
                    if is_wildlife_confirmed:
                        last_detection_time = current_time

                    val_to_show = int(float(val)) if val is not None else 0
                    if current_time - last_detection_time < 2.0:
                        try: ser.write(f"DETECT:{val_to_show}\n".encode('utf-8'))
                        except: pass
                    else:
                        try: ser.write(f"RESET:{val_to_show}\n".encode('utf-8'))
                        except: pass
                        
                    last_render_time = current_time

                ui.root.update()
                time.sleep(0.001)
        except KeyboardInterrupt:
            pass
        finally:
            ser.close()
            ui.close()
        return

    # ── 실시간 감지 루프 (Mode 5) ───────────────────────────
    step_analyzer_geo = StepDurationAnalyzer()
    bistable_engine_geo = BistableDoubleWellEngine()

    bistable_engine_geo.a = opt_a_geo
    bistable_engine_geo.b = opt_b_geo
    acf_analyzer_geo  = ACFPeriodicityAnalyzer()
    # EMA 기반 DC 추적기 — 느린 드리프트(low-freq bias)를 실시간으로 추적·제거합니다.
    # list로 감주서 while 루프 안에서도 업데이트가 가능합니다.
    _dc_ema_geo = [None]
    _DC_ALPHA   = 0.002   # 매우 느린 EMA → 0.2% 갱신 = ~500샘플 시정수 (≈5초 @ 100Hz)

    def on_band_change(bands):
        sdft_filter_geo.manual_bands = bands

    ui.setup_live_detection(on_manual_band_change=on_band_change)
    last_render_time = time.time()
    last_detection_time = 0.0
    print('시스템 엔진 가동 중 (지오폰 A0 분석)...')

    try:
        while ui.is_alive():
            data_updated = False
            # 쌓인 데이터를 모두 읽어서 버퍼에 정상적인 속도(100Hz)로 채웁니다.
            while ser.in_waiting > 0:
                ln = ser.readline().decode('utf-8', errors='ignore').strip()
                if ln:
                    val = parse_serial_line(ln, 0)
                    if val is not None:
                        raw_centered = float(val) - zero_geo
                        # EMA로 저주파 드리프트(DC bias)를 추적하고 실시간 제거
                        if _dc_ema_geo[0] is None:
                            _dc_ema_geo[0] = raw_centered
                        else:
                            _dc_ema_geo[0] = (1.0 - _DC_ALPHA) * _dc_ema_geo[0] + _DC_ALPHA * raw_centered
                        data_buffer_geo.append(raw_centered - _dc_ema_geo[0])
                        raw_val_buffer_geo.append(float(val))
                        data_updated = True

            current_time = time.time()
            # 버퍼가 완전히 채워진 뒤에만 분석 시작 (초기 0-패딩 왜곡 방지 및 Matplotlib 플롯 크기 불일치 방지)
            if data_updated and len(data_buffer_geo) >= config.BUFFER_SIZE and \
                    (current_time - last_render_time >= config.RENDER_INTERVAL):
                # 1. 지오폰 분석 파이프라인
                raw_signal_geo = np.array(data_buffer_geo)
                # EMA DC 제거를 이미 버퍼 단계에서 수행했으므로 추가 mean subtraction 불필요
                signal_geo = raw_signal_geo
                # 미세 진동 강조용 비선형 스케일링 (선택적)
                signal_geo = signal_geo * (1.0 - np.exp(-np.abs(signal_geo) / 2.5))
                
                act_idx_geo, is_rec_geo, step_comp_geo, dur_geo, durations_geo = step_analyzer_geo.analyze(signal_geo, current_time)
                avg_dur_geo = sum(durations_geo)/len(durations_geo) if durations_geo else 0.0
                filtered_signal_geo, M_t_geo, clean_fft_mag_geo, transient_detected_geo = sdft_filter_geo.process(signal_geo)
                
                white_noise_geo = np.random.normal(0, opt_sigma_geo, len(filtered_signal_geo))
                x_arr_tot_geo, x_arr_noi_geo, N_t_geo, K_t_geo = bistable_engine_geo.process_buffer(filtered_signal_geo, white_noise_geo)
                net_events_geo = max(0, N_t_geo - K_t_geo)
                
                telegraph_signal_geo = np.sign(x_arr_tot_geo)
                acf_r_geo, cadence_geo = acf_analyzer_geo.compute(telegraph_signal_geo)

                # 2. GUI 업데이트 (지오폰 데이터 전달)
                ui.update(
                    np.array(raw_val_buffer_geo), # 원래의 val 값을 그래프에 띄움
                    x_arr_tot_geo,
                    M_t_geo,
                    clean_fft_mag_geo,
                    sdft_filter_geo.M_avg,
                    sdft_filter_geo.detected_noise_bands,
                    is_rec_geo,
                    step_comp_geo,
                    dur_geo,
                    avg_dur_geo,
                    len(durations_geo),
                    transient_detected_geo,
                    N_t_geo, K_t_geo, net_events_geo, acf_r_geo, cadence_geo
                )

                # 3. 최종 검출 결과 출력 및 아두이노 LCD 제어 (R >= 0.6 일 때 감지 판정)
                is_wildlife_confirmed = (acf_r_geo >= 0.6)
                if is_wildlife_confirmed:
                    last_detection_time = current_time

                val_to_show = int(float(val)) if val is not None else 0

                # 마지막 감지 후 2초 동안은 불 켜진 상태(DETECT) 유지
                if current_time - last_detection_time < 2.0:
                    if is_wildlife_confirmed:
                        print(f'🐾 [동물 확정] ACF_R={acf_r_geo:.2f} >= 0.6, Val={val_to_show}')
                    try:
                        ser.write(f"DETECT:{val_to_show}\n".encode('utf-8'))
                    except Exception as e:
                        print(f"⚠️ LCD 전송 오류: {e}")
                else:
                    # 2초가 지나면 감지 해제 (RESET)
                    try:
                        # 평상시에도 LCD에 val 값이 뜨도록 RESET 명령 뒤에 값을 붙여서 전송합니다.
                        ser.write(f"RESET:{val_to_show}\n".encode('utf-8'))
                    except Exception as e:
                        pass

                last_render_time = current_time

            # 아두이노 데이터가 들어오지 않더라도 GUI 이벤트가 처리되도록 함 (응답없음 방지)
            ui.root.update()
            time.sleep(0.001)

    except KeyboardInterrupt:
        print('파이프라인 종료...')
    finally:
        ser.close()
        ui.close()


if __name__ == '__main__':
    main()
