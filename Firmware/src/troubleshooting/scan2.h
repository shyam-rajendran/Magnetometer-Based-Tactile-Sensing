#include <Arduino.h>
#include <Wire.h>

#define TCA_ADDRESS 0x70

// Select TCA channel
void tcaSelect(uint8_t channel)
{
    if (channel > 7) return;

    Wire.beginTransmission(TCA_ADDRESS);
    Wire.write(1 << channel);
    Wire.endTransmission();
}

// Scan current I2C bus
void scanChannel(uint8_t channel)
{
    tcaSelect(channel);

    Serial.print("\n--- Scanning Channel ");
    Serial.print(channel);
    Serial.println(" ---");

    int devicesFound = 0;

    for (uint8_t addr = 1; addr < 127; addr++)
    {
        Wire.beginTransmission(addr);
        uint8_t error = Wire.endTransmission();

        if (error == 0)
        {
            Serial.print("Device found at 0x");
            if (addr < 16) Serial.print("0"); // formatting
            Serial.println(addr, HEX);

            devicesFound++;
        }
    }

    if (devicesFound == 0)
    {
        Serial.println("No devices found.");
    }
    else
    {
        Serial.print("Total devices: ");
        Serial.println(devicesFound);
    }
}

void setup()
{
    Serial.begin(115200);
    Wire.begin(15, 2);  // SDA, SCL for ESP32

    Serial.println("TCA9548A Multiplexer Scanner");

                // Check if TCA itself is alive
            Wire.beginTransmission(TCA_ADDRESS);
            if (Wire.endTransmission() == 0)
            {
                Serial.println("TCA9548A detected!");
            }
            else
            {
                Serial.println("TCA NOT detected!");
            }

    for (uint8_t i = 0; i < 8; i++)
    {
        scanChannel(i);
        delay(200);  // small delay for stability
    }
}

void loop()
{
}