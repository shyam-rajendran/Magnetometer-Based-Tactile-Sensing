#include <Arduino.h>
#include <Wire.h>

#define SDA_PIN     15
#define SCL_PIN     2
#define TCA_ADDR    0x70
#define MLX_ADDR    0x0F
#define NUM_SENSORS 5

void tcaSelect(uint8_t channel) {
    Wire.beginTransmission(TCA_ADDR);
    Wire.write(1 << channel);
    Wire.endTransmission();
    delay(5);
}

void tcaDisableAll() {
    Wire.beginTransmission(TCA_ADDR);
    Wire.write(0x00);
    Wire.endTransmission();
}

bool mlxInit() {
    Wire.beginTransmission(MLX_ADDR);
    Wire.write(0x80);  // EX
    Wire.endTransmission();
    delay(20);

    Wire.beginTransmission(MLX_ADDR);
    Wire.write(0xF0);  // RT
    Wire.endTransmission();
    delay(20);

    // NOP to confirm alive
    Wire.beginTransmission(MLX_ADDR);
    Wire.write(0x00);
    Wire.endTransmission();
    delay(10);

    Wire.requestFrom((uint8_t)MLX_ADDR, (uint8_t)1);
    if (!Wire.available()) return false;
    Wire.read();
    return true;
}

bool mlxRead(float &Bx, float &By, float &Bz) {
    // Send SM, read status byte back in same transaction
    Wire.beginTransmission(MLX_ADDR);
    Wire.write(0x3E);  // SM XYZ
    Wire.endTransmission();
    delay(20);  // wait for conversion

    // Read 7 bytes directly — RM is implied after SM on MLX90393
    // when you requestFrom without sending RM first
    Wire.requestFrom((uint8_t)MLX_ADDR, (uint8_t)7);
    
    unsigned long t = millis();
    while (Wire.available() < 7) {
        if (millis() - t > 100) {
            Serial.printf("  timeout, got %d bytes\n", Wire.available());
            return false;
        }
    }

    uint8_t status = Wire.read();
    Serial.printf("  status=0x%02X ", status);

    int16_t rawX = (Wire.read() << 8) | Wire.read();
    int16_t rawY = (Wire.read() << 8) | Wire.read();
    int16_t rawZ = (Wire.read() << 8) | Wire.read();

    Bx = rawX * 0.15f;
    By = rawY * 0.15f;
    Bz = rawZ * 0.15f;

    return true;
}

void setup() {
    Serial.begin(115200);
    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(100000);
    delay(500);

    Serial.println("Initializing sensors...");
    for (int ch = 0; ch < NUM_SENSORS; ch++) {
        tcaSelect(ch);
        if (mlxInit()) {
            Serial.printf("ch%d: OK\n", ch);
        } else {
            Serial.printf("ch%d: FAILED\n", ch);
        }
        tcaDisableAll();
        delay(20);
    }
    Serial.println("Ready");
}

void loop() {
    float Bx, By, Bz;

    for (int ch = 0; ch < NUM_SENSORS; ch++) {
        tcaSelect(ch);
        Serial.printf("ch%d:", ch);

        if (mlxRead(Bx, By, Bz)) {
            Serial.printf("Bx=%.2f By=%.2f Bz=%.2f\n", Bx, By, Bz);
        } else {
            Serial.println("FAILED");
        }

        tcaDisableAll();
        delay(5);
    }

    Serial.println("---");
    delay(200);
}