#include <Arduino.h>
#include <Wire.h>

#define TCA_address 0x70

void scanner()
{
    int devicesFound=0;
    
     Serial.println("Scanning I2C bus...");
    for (uint8_t add=0; add<128; add++)
    {
        Wire.beginTransmission(add);
        int error = Wire.endTransmission();
        if (error == 0) 
        {
            devicesFound++;
            Serial.print("I2C device found at address: 0x");
            Serial.println(add, HEX);
        }
    }
    Serial.print("Total devices found: ");
    Serial.println(devicesFound);
}


void multiplexer(uint8_t channel)
{
    Wire.beginTransmission(TCA_address);
    Wire.write(1 << channel);
    Wire.endTransmission();

}

void setup()
{
       
    Serial.begin(115200);
    Wire.begin(15,2);

    int devicefind=0;

    //scanner();

    for (int i=0; i<8;i++)
    {
        multiplexer(i);
        Serial.print("Channel: ");
        Serial.print(i);

        bool deviceFound=false;

        for (int j=0;j<127;j++)
        {
            Wire.beginTransmission(j);
            if (Wire.endTransmission()==0)
            {
                if(j==0xF || j==0x48 || j==0xC)
                {
                Serial.print(" -> Device detected: 0x");
                Serial.println(j,HEX);
                deviceFound=true;
                }  
            } 
        }
        if (!deviceFound)
        {
            Serial.println(" -> No Devices");
        }
    }
}

void loop() 
{
   
}



