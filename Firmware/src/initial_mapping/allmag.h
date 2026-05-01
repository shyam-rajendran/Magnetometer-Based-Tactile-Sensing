#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90393.h>

#define SDA_PIN      15
#define SCL_PIN      2
#define MUX_ADDR     0x70
#define MLX_ADDR     0x0F

#define NUM_SENSORS       5
#define BASELINE_SAMPLES  100
#define BASELINE_DELAY_MS 10
#define LOOP_DELAY_MS     10   // 10 Hz

const uint8_t MUX_CHANNELS[NUM_SENSORS] = {0, 1, 2, 3, 4};

Adafruit_MLX90393 sensors[NUM_SENSORS];

float baseline_bx[NUM_SENSORS] = {0};
float baseline_by[NUM_SENSORS] = {0}; 
float baseline_bz[NUM_SENSORS] = {0};

// ─── MUX ─────────────────────────────────────────────────────────────────────

void muxSelect(uint8_t ch) {
    Wire.beginTransmission(MUX_ADDR);
    Wire.write(1 << ch);
    Wire.endTransmission();
}

void muxDisable() {
    Wire.beginTransmission(MUX_ADDR);
    Wire.write(0x00);
    Wire.endTransmission();
}

// ─── INIT ────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    Wire.begin(SDA_PIN, SCL_PIN);
    delay(100);

    Serial.println("# Initializing sensors...");

    // Init each sensor through its mux channel
    for (int i = 0; i < NUM_SENSORS; i++) {
        muxSelect(MUX_CHANNELS[i]);
        delay(10);

        if (!sensors[i].begin_I2C(MLX_ADDR)) {
            Serial.printf("# ERROR: sensor %d (ch%d) not found!\n", i, MUX_CHANNELS[i]);
        } else {
            Serial.printf("# Sensor %d (ch%d) OK\n", i, MUX_CHANNELS[i]);
        }
    }

    // Capture baseline for each sensor
    Serial.println("# Capturing baselines — hold still, no magnets nearby...");

    for (int i = 0; i < NUM_SENSORS; i++) {
        muxSelect(MUX_CHANNELS[i]);
        delay(10);

        float bx_sum = 0, by_sum = 0, bz_sum = 0;
        int valid = 0;

        for (int s = 0; s < BASELINE_SAMPLES; s++) {
            float x, y, z;
            if (sensors[i].readData(&x, &y, &z)) {
                bx_sum += x;
                by_sum += y;
                bz_sum += z;
                valid++;
            }
            delay(BASELINE_DELAY_MS);
        }

        if (valid == 0) {
            Serial.printf("# ERROR: no valid baseline samples for sensor %d\n", i);
            continue;
        }

        baseline_bx[i] = bx_sum / valid;
        baseline_by[i] = by_sum / valid;
        baseline_bz[i] = bz_sum / valid;

        Serial.printf("# Sensor %d baseline: Bx=%.2f By=%.2f Bz=%.2f uT\n",
                      i, baseline_bx[i], baseline_by[i], baseline_bz[i]);
    }

    muxDisable();

    Serial.println("# Ready.");
    Serial.println("# Output: ch,Bx,By,Bz (uT, baseline-subtracted)");
    Serial.println("# ---");
}

// ─── LOOP ────────────────────────────────────────────────────────────────────

void loop() {
    for (int i = 0; i < NUM_SENSORS; i++) {
        muxSelect(MUX_CHANNELS[i]);

        float x, y, z;
        if (sensors[i].readData(&x, &y, &z)) {
            Serial.printf("%d,%.4f,%.4f,%.4f\n",
                          i,
                          x - baseline_bx[i],
                          y - baseline_by[i],
                          z - baseline_bz[i]);
        } else {
            Serial.printf("# sensor %d read failed\n", i);
        }
    }

    muxDisable();
    delay(LOOP_DELAY_MS);
}   