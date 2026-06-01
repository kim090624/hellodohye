// =====================================================================
// 야생동물 감지 시스템 - 아두이노 센서 펌웨어 (LCD 경보 시스템 추가)
// Geophone (A0) 단일 채널, 100Hz 안정적 샘플링 + I2C LCD 제어
// =====================================================================

#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// I2C 주소가 0x27 또는 0x3F인 1602 LCD 객체 생성
LiquidCrystal_I2C lcd(0x27, 16, 2);

void setup() {
  Serial.begin(115200); // 고속 통신
  
  // LCD 초기화
  lcd.init();
  lcd.backlight();
  lcd.clear();
  
  // 초기 대기 화면 표시 (감지되지 않았을 때)
  lcd.setCursor(0, 0);
  lcd.print("Monitering...");
  lcd.setCursor(0, 1);
  lcd.print("Val: 0");
  lcd.noBacklight(); // 감지 안 될 때는 백라이트 off 상태
}

void loop() {
  unsigned long t_start = millis(); // 루프 시작 시간 기록

  // ── PC(Python)로부터 LCD 제어 명령어 수신 처리 ─────────────────
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("DETECT:")) {
      // 감지되었을 때: DETECT:XXX (첫번째 줄에 Animal Detected! / 파란색 백라이트 on / 두번째 줄에 Val: XXX)
      String valStr = cmd.substring(7);
      lcd.backlight(); // 파란색 백라이트 on 상태
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Animal Detected!");
      lcd.setCursor(0, 1);
      lcd.print("Val: " + valStr);
    } 
    else if (cmd.startsWith("RESET:")) {
      // 감지되지 않았을 때 (첫번째 줄에 Monitering... / 백라이트 off / 두번째 줄에 파이썬에서 보낸 원본 값 표시)
      String valStr = cmd.substring(6);
      lcd.noBacklight(); // 백라이트 off 상태
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Monitering...");
      lcd.setCursor(0, 1);
      lcd.print("Val: " + valStr);
    }
  }

  // ── A0: Geophone (고임피던스 수동 코일 센서) ────────────────────────
  long sum = 0;
  for (int i = 0; i < 5; i++) {
    sum += analogRead(A0);
    delay(1); // 각 샘플 사이 1ms 대기
  }
  int val0 = sum / 5; // 평균값

  // 파이썬으로 단일 정수값 전송
  Serial.println(val0);

  // ── 정확히 100Hz(10ms 주기) 유지를 위한 나머지 대기 ────────────────
  unsigned long elapsed = millis() - t_start;
  if (elapsed < 10) {
    delay(10 - elapsed);
  }
}
