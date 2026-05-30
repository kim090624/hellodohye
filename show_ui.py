# -*- coding: utf-8 -*-
"""
아두이노 하드웨어 연결 없이 새롭게 개선된 GUI 화면을 모니터링하기 위한 테스트 스크립트
"""
import sys
import os

# 모듈 탐색 경로 확보
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ui_manager import WildlifeUI

def main():
    print("🎨 [UI 프리뷰 가동] 아두이노 포트 연결 없이 GUI 초기 화면을 엽니다...")
    
    # UI 매니저 생성 및 모드 선택창 가동
    ui = WildlifeUI()
    choice = ui.wait_for_choice()
    
    if choice:
        print(f"\n👉 사용자가 선택한 모드 번호: {choice}번")
        print("하드웨어가 연결되지 않은 상태이므로 프리뷰가 종료됩니다.")
    else:
        print("\n✕ 사용자가 창을 종료했습니다.")

if __name__ == '__main__':
    main()
