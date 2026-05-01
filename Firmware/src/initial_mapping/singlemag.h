#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90393.h>

#define SDA_PIN      15
#define SCL_PIN      2
#define MUX_ADDR     0x70
#define MLX_ADDR     0x0F

#define BASELINE_SAMPLES  100
#define BASELINE_DELAY_MS 10
#define LOOP_DELAY_MS     50   // 20 Hz

Adafruit_MLX90393 sensor;

float baseline_bx = 0, baseline_by = 0, baseline_bz = 0;

void muxSelect(uint8_t ch) {
    Wire.beginTransmission(MUX_ADDR);
    Wire.write(1 << ch);
    Wire.endTransmission();
}

void captureBaseline() {
    Serial.println("# Capturing baseline — keep PCB still, no magnet nearby...");

    float bx_sum = 0, by_sum = 0, bz_sum = 0;
    int valid = 0;

    for (int i = 0; i < BASELINE_SAMPLES; i++) {
        float x, y, z;
        if (sensor.readData(&x, &y, &z)) {
            bx_sum += x;
            by_sum += y;
            bz_sum += z;
            valid++;
        }
        delay(BASELINE_DELAY_MS);
    }

    if (valid == 0) {
        Serial.println("# ERROR: no valid samples during baseline — check sensor");
        while (true) delay(1000);
    }

    baseline_bx = bx_sum / valid;
    baseline_by = by_sum / valid;
    baseline_bz = bz_sum / valid;

    Serial.printf("# Baseline captured (%d samples): Bx=%.2f  By=%.2f  Bz=%.2f uT\n",
                  valid, baseline_bx, baseline_by, baseline_bz);
    Serial.println("# Ready. Output: Bx, By, Bz (uT, baseline-subtracted)");
    Serial.println("# ---");
}

void setup() {
    Serial.begin(115200);
    Wire.begin(SDA_PIN, SCL_PIN);

    muxSelect(0);

    if (!sensor.begin_I2C(MLX_ADDR)) {
        Serial.println("# ERROR: MLX90393 not found — check wiring!");
        while (true) delay(1000);
    }
    Serial.println("# MLX90393 found on channel 0");

    captureBaseline();
}

void loop() {
    float x, y, z;

    if (sensor.readData(&x, &y, &z)) {
        Serial.printf("%.4f,%.4f,%.4f\n",
                      x - baseline_bx,
                      y - baseline_by,
                      z - baseline_bz);
    } else {
        Serial.println("# read failed");
    }

    delay(LOOP_DELAY_MS);
}