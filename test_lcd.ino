#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// I2C 주소가 0x27 (또는 0x3F)인 1602 LCD 객체 생성
LiquidCrystal_I2C lcd(0x27, 16, 2);

void setup() {
  // LCD 초기화
  lcd.init();
  
  // 백라이트 항상 켜두기
  lcd.backlight();
  
  // 화면 지우기
  lcd.clear();
  
  // 첫 번째 줄 출력
  lcd.setCursor(0, 0);
  lcd.print("Animal Detected!");
  
  // 두 번째 줄 출력
  lcd.setCursor(0, 1);
  lcd.print("Val: 678");
}

void loop() {
  // 아무런 추가 동작 없이 상태를 영구적으로 유지
  delay(1000);
}
