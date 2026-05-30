# -*- coding: utf-8 -*-
"""
제 4.1.1절: 비선형 이중 우물 퍼텐셜(Double-Well Potential) 적분 엔진
================================================================================
확률 공명(Stochastic Resonance, SR) 현상을 시뮬레이션하기 위한 수학적 기반 및 수치해석 솔버
================================================================================

[1. 이중 우물 퍼텐셜 함수 공식]
    U(x) = (b / 4) * x^4 - (a / 2) * x^2

    * 퍼텐셜에 의한 복원력(경사면의 힘): F_pot(x) = -dU/dx = a*x - b*x^3
    * 안정된 우물 바닥 (입자가 쉬는 곳): x_m = +/- sqrt(a/b)
    * 가운데 에너지 장벽 (넘어야 할 산): x = 0 (장벽의 높이: dU = a^2 / 4b)

[2. 비선형 랑주뱅 미분방정식 (Langevin Equation)]
    dx/dt = a*x - b*x^3 + Force(t),  Force(t) = [S(t) + N(t)] * force_scalar

[3. 오일러-마루야마(Euler-Maruyama) 수치 적분]
    x_{t+1} = x_t + (a*x_t - b*x_t^3 + Force_t) * dt

[성능 주의사항]
    process_buffer는 비선형 점화식(x_{i+1} = f(x_i))이므로 완전 병렬화가 불가능합니다.
    대신 NumPy 기반 최적화된 for 루프로 구현되어 있으며,
    BUFFER_SIZE=300 기준으로 호출 1회당 약 0.1ms 수준으로 충분히 빠릅니다.
"""
import numpy as np
import config


class BistableDoubleWellEngine:
    def __init__(self):
        """
        보고서 4.1.1절에 맞춰 수치 시뮬레이터 파라미터를 초기화합니다.
        (모든 상수는 config.py에서 통합 관리됩니다.)
        """
        self.a = config.POTENTIAL_A
        self.b = config.POTENTIAL_B
        self.dt = config.DT
        self.force_scalar = config.FORCE_SCALAR
        self.bound = config.BOUND

        # 이전 프레임에서 계산된 입자의 마지막 위치를 기억해두는 변수입니다.
        # 이를 통해 버퍼(프레임)가 넘어가도 입자의 움직임이 물리적으로 끊기지 않고 연속되도록 보장합니다.
        self.x_state_total = 1.0
        self.x_state_noise = 1.0

    def process_buffer(self, filtered_signal, white_noise):
        """
        실시간으로 들어오는 센서 데이터 버퍼(배열) 전체에 대해 오일러-마루야마 적분을 실행합니다.

        반환값:
            x_arr_total: 신호+소음의 힘을 받아 움직인 입자의 궤적 (발소리가 포함된 실제 상태)
            x_arr_noise: 순수 배경 소음만으로 움직인 입자의 궤적 (비교를 위한 대조군)
            N_t: 신호+소음 상태에서 입자가 장벽(0)을 넘어 전이한 총 횟수
            K_t: 소음 단독 상태에서 우연히 장벽을 넘은 기준 전이 횟수
        """
        buffer_size = len(filtered_signal)
        x_arr_total = np.empty(buffer_size)
        x_arr_noise = np.empty(buffer_size)

        a   = self.a
        b   = self.b
        dt  = self.dt
        bnd = self.bound
        fs  = self.force_scalar

        # 센서에서 들어온 원본 스케일이 너무 커서 x^3 항이 무한대로 발산하는 것을 막기 위해
        # force_scalar를 곱해 연속 수학 모델에 맞는 안전한 스케일로 외력을 미리 축소/조정합니다.
        force_total = (filtered_signal + white_noise) * fs
        force_noise = white_noise * fs

        xt = self.x_state_total
        xn = self.x_state_noise

        # -------------------------------------------------------------------
        # 오일러-마루야마 적분: 비선형 점화식이므로 순차(sequential) 계산 필수
        # x_{i+1} = x_i + (a*x_i - b*x_i^3 + F_i) * dt
        # BUFFER_SIZE=300 기준 약 0.05~0.15ms — UI 블로킹 없음
        # -------------------------------------------------------------------
        for i in range(buffer_size):
            # 전체 신호 (신호 + 소음)
            xt = xt + (a * xt - b * (xt * xt * xt) + force_total[i]) * dt
            if xt >  bnd: xt =  bnd
            elif xt < -bnd: xt = -bnd
            x_arr_total[i] = xt

            # 대조군 (순수 소음만)
            xn = xn + (a * xn - b * (xn * xn * xn) + force_noise[i]) * dt
            if xn >  bnd: xn =  bnd
            elif xn < -bnd: xn = -bnd
            x_arr_noise[i] = xn

        # 다음 프레임을 위해 상태 저장
        self.x_state_total = xt
        self.x_state_noise = xn

        # -------------------------------------------------------------------
        # 전이 빈도 함수 N(t) / K(t) 계산 (Zero-crossing 횟수)
        # 입자가 x=0 에너지 장벽을 넘을 때마다 카운트 (+1)
        # -------------------------------------------------------------------
        N_t = int(np.sum((x_arr_total[:-1] >= 0) != (x_arr_total[1:] >= 0)))
        K_t = int(np.sum((x_arr_noise[:-1] >= 0) != (x_arr_noise[1:] >= 0)))

        return x_arr_total, x_arr_noise, N_t, K_t

    def reset_states(self):
        """강제로 입자의 위치를 평상시 기저 상태(우물 바닥)로 초기화합니다."""
        self.x_state_total = 1.0
        self.x_state_noise = 1.0
