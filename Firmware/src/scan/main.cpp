#include <Arduino.h>
#include <Wire.h>

#define SDA 15
#define SCL 2

void setup()
{
  Serial.begin(115200);
  Wire.begin(SDA,SCL);
  
  int devices=0;
  for (int i=0; i<127; i++)
  {
    Wire.beginTransmission(i);
    int error=Wire.endTransmission();
    if (error==0)
    { 
      devices++;
      Serial.print("Device found at: 0x");
      Serial.println(i, HEX);
    }
  }
  Serial.print("total devices: ");
  Serial.println(devices);
}

void loop()
{

}
