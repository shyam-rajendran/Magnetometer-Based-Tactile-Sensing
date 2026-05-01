#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90393.h>
#include <SparkfunTMP102.h>

#define SDA 15
#define SCL 2

#define MUX_ADD 0x70
#define MLX_ADD 0xF
#define TMP_ADD 0x48

unsigned long start, totalstart, elapsed, totalelapsed;  //millis timing variables

Adafruit_MLX90393 mlx_sensor[5];
TMP102 tmp_sensor[2];

float baseline[5][3];  //2d array for sensor x,y,z; 5sensors 3axis

void channel_status()
{
    for (uint8_t channel=0; channel<8; channel++){
        Wire.beginTransmission(MUX_ADD);
        Wire.write(1<<channel);
        Wire.endTransmission(); 
       
        bool deviceFound=false;

        for (uint8_t address=0; address<127; address++){ 
            Wire.beginTransmission(address);
           
            if (Wire.endTransmission()==0 && (address==TMP_ADD || address==MLX_ADD)){
                Serial.printf("> Channel #%d: 0x%02X \n", channel, address);
                deviceFound=true;
            }
        }

        if (!deviceFound) Serial.printf("> Channel #%d: No Device\n",channel);
    }
}


void muxcheck()
{
    Wire.beginTransmission(0x70);
    uint8_t error=Wire.endTransmission();
    if (error==0) {
        Serial.println("TCA9548 Active");
        delay(500);
        channel_status();
    }
    else {
        Serial.println("TCA9548 Inactive");
        while(true) delay(1000);
    }
}

void selectchannel(uint8_t channel)
{
    Wire.beginTransmission(MUX_ADD);
    Wire.write(1<<channel);
    Wire.endTransmission();
    delay(10);
}

void calibratebaseline()
{
    const int samples=100;

    Serial.println("\n--Capturing Sensor Baseline--");
    
    totalstart=millis();
    for (int i=0; i<5; i++){
        float BXsum = 0, BYsum = 0, BZsum = 0;

        selectchannel(i);
        int valid = 0; //this variable is being used for the baseline averaging not the samples
        start=millis();

        for (int k=0; k<samples; k++){
            float x,y,z;

            if (mlx_sensor[i].readData(&x, &y, &z)) {
                BXsum+=x;
                BYsum+=y;
                BZsum+=z;
                valid++;
            }
            delay(50);
        }
        if (valid==0) Serial.printf("ERROR: No valid baseline for channel %d MLX \n", i);        
        
        if (valid>0){
        baseline[i][0]=BXsum/valid;
        baseline[i][1]=BYsum/valid;
        baseline[i][2]=BZsum/valid;
        }

        elapsed=millis()-start;
        totalelapsed=millis()-totalstart;
        Serial.printf("CH %d Baseline Captured [%d samples, %.2f sec | %.2f sec]: %.2fuT, %0.2fuT, %0.2fuT \n", i, valid, elapsed/1000.0f, totalelapsed/1000.0f, baseline[i][0], baseline[i][1], baseline[i][2]);
    }
    Serial.println("--All Baseline Values Captured!--");
}

void setup() 
{
    Serial.begin(250000);
    Wire.begin(SDA,SCL);

    muxcheck(); //test device recognition

    Serial.println("\n--Initilializing Sensors--\n");

    for (uint8_t i=0; i<5;i++){     //mlx init
        selectchannel(i);
        if (mlx_sensor[i].begin_I2C(MLX_ADD)){
            Serial.printf("# Channel %d: MLX init DONE\n", i);
                    
            mlx_sensor[i].setGain(MLX90393_GAIN_4X);
            mlx_sensor[i].setResolution(MLX90393_X, MLX90393_RES_16);
            mlx_sensor[i].setResolution(MLX90393_Y, MLX90393_RES_16);
            mlx_sensor[i].setResolution(MLX90393_Z, MLX90393_RES_16);

            mlx_sensor[i].setOversampling(MLX90393_OSR_3);   //internal averages, higher=slower=accurate
            mlx_sensor[i].setFilter(MLX90393_FILTER_5);      //lowpass filter, higher=slower=smoothcurve
        }      
        else{
            Serial.printf("# Channel %d: MLX init FAILED\n", i);
        }
    }

    for(uint8_t j=5; j<7; j++){  //tmp init
        selectchannel(j);
        if (tmp_sensor[j-5].begin()) 
        {
            Serial.printf("# Channel %d: TMP init DONE \n", j);
            tmp_sensor[j-5].setConversionRate(3); //freq set to 8Hz 
            tmp_sensor[j-5].wakeup();
        }

        else Serial.printf("# Channel %d: TMP init failed \n", j);
    }

    //calibratebaseline();  //get all the baseline values 

}


void loop()
{
    String output= "";

    // baseline's getting captured in the python script not here
    // for (int i=0; i<5; i++){
    //     selectchannel(i);

    //     float x, y, z;
    //     if(mlx_sensor[i].readData(&x, &y, &z)){
    //         float dx = x - baseline[i][0];
    //         float dy = y - baseline[i][1];
    //         float dz = z - baseline[i][2];
            
    //         output+="#"+String(i)+","+String(dx)+","+String(dy)+","+String(dz)+",";
    //     }
    // }
    for (int i=0; i<5; i++){
        selectchannel(i);

        float x, y, z;
        if(mlx_sensor[i].readData(&x, &y, &z)){
            float dx = x;
            float dy = y; 
            float dz = z; 
        
            output+="#"+String(i)+","+String(dx)+","+String(dy)+","+String(dz)+",";
        }
    }
    selectchannel(5);
    output+="T1,"+String(tmp_sensor[0].readTempC());
    selectchannel(6);
    output+=+",T2,"+String(tmp_sensor[1].readTempC());
    
    Serial.println(output);
    //output format: #0,dx,dy,dz,#1,dx,dy,dz,#2,dx,dy,dz,#3,dx,dy,dz,#4,dx,dy,dz,T1,T1_data,T2,T2_data
}