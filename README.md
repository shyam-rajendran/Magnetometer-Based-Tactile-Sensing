# Tactile Sensor

<table align="center">
  <tr>
    <td><img src="https://github.com/user-attachments/assets/d051cf76-6250-4eae-99dd-382642c0f2a3" height="300"/></td>
    <td><img src="https://github.com/user-attachments/assets/c37f32a2-53b2-4c65-9f92-d1a859c695e1" height="300"/></td>
    <td><img src="https://github.com/user-attachments/assets/afbd89a3-7457-42cd-a9aa-89590a2e5a8e" height="300"/></td>
    <td><img src="https://github.com/user-attachments/assets/57f90979-b273-4339-96dc-6a769467078b" height="300"/></td>
  </tr>
</table>


This tactile sensor utilizes magnetometers as a means to detect force vectors acting on the surface. This is done through the combination of magnetometers and a silicone elastomer embedded with neodymium magnets.

This project is heavily inspired by the work of [AnySkin](https://any-skin.github.io/), [ReSkin](https://reskin.dev/) & [Bio-Skin](https://williamalexanda.github.io/Bio-Skin/) and the project is intended to be further developed for magnetic locaization, which as of now is still in development. 

The PCB was designed entirely in Altium Designer. I've included the entire Altium project file including the design files, libraries, 3D models, documentations, schematics, gerber.

## Repo Structure
```
1. CAD Files (folder) - Contains the enclosure and mold SolidWorks Part & Assembly files
    a. Mold Designs
        - New Mold Wall 1.SLDPRT
        - New Mold Wall 2.SLDPRT
        - New Mold Assembly.SLDASM
    b. New Enclosure
        - assembly whole thing.SLDASM
        - Bottom enclosure.SLDPRT
        - initial mold assembly.SLDASM
        - Mold Top.SLDPRT
        - Top enclosure.SLDPRT

2. Docs (folder)
   Datasheets of the components used

3. Fabrication Files (folder)
   Gerber file used for manufacturing the PCB

4. Fimrware (folder)
   PlatformIO-VSCode based code environment

5. Libraries (folder) - Includes all the libraries made for PCB design within Altium
    a. 6pin jst.IntLib
    b. PcbLib1.PcbLib
    c. Schlib1.SchLib

6. PCB Design Files (folder)
    a. AnySkin.PcbDoc
    b. AnySkin.PrjPcb
    c. Interface.SchDoc
    d. Schematics.pdf
    e. Sensor Suite.SchDoc

7. Software (folder)
   Python Scripts for force visualization

```


## Sensor ICs used
- MLX90393 (6 Axis Magnetometer) 
- TMP102 (Digital Temperature Sensor)
- TCA9548 (I2C Multiplexer)
- AMS1117 (Low Dropout Voltage Regulator)

## Pinout & Address
The I2C addresses of the ICs are physically set to the specified addresses below but can be changed to alternate I2C address through hardwiring (not recommended).

All the magnetometers share the same I2C address, therefore to prevent address conflict, each magnetometer is connected to the multiplexer and has an individual assigned channel which are selected using bitwise left shift operation. 


_**I2C Peripheral Addresses**_
| Sensor  | I2C Address |
| ------- |------|
| MLX90393| 0X0F |
| TMP102  | 0x48 |
| TCA9548 | 0x71 |

\
_**Multiplexer Channel Designation**_
<table>
  <tr>
    <td>

**Multiplexer Channels**
| Channel No | Sensor Address |
|------------|--------|
| Channel 0  | 0x0F   |
| Channel 1  | 0x0F   |
| Channel 2  | 0x0F   |
| Channel 3  | 0x0F   |
| Channel 4  | 0x0F   |
| Channel 5  | 0x48   |
| Channel 6  | 0x48   |
| Channel 7  | Unassigned |

  </td>
  <td>

<img src="https://github.com/user-attachments/assets/981dae9c-9ab3-46c9-a430-664b47bb5daa" alt="channel desig" width="450"/>

  </td>
  </tr>
</table>

## Silicone Elastomer Fabrication
For this project, a two part silicone elastomer is used ([Amazon Link](https://www.amazon.in/RANA-POLYMERS-Silicone-Non-Toxic-Odorless-1/dp/B0F47XKC3K?source=ps-sl-shoppingads-lpcontext&smid=AQNXSE8C5E2UX)) which is mixed in a 1:1 ratio and is then poured into the 3D printed mold design. It roughly takes 3-4 hours for curing and can be easily taken out post curing. 

The elastomer is layered in two parts, such that an array of Neodymium magnets are sandwiched in between.  The Nd magnets are arranged in a 3X3 format such that 5 Nd magnets lie directly above the magnetometer and the remaining 4 Nd magnets lie in the interstitial space between them.

The entire process can be divided into three stages:

1. `First Elastomer Layer` 
    -  The mixture of Part A & B is poured into the mold enclosure upto 3 mm height and is then allowed to cure.
2. `Magnets Arrangement` 
    - Once the initial elastomer layer is cured, the magnets are arranged manually and are forcefully sticked onto the top surface of the first silicone layer using adhesive. 
3. `Second Elastomer Layer`
    - The second elastomer layer is poured on top of the magnets, filling out the entire remaining volume of the mold. Allow it to cure for 3-4 hours and then the mold walls can be removed.

<table align="center">
  <tr>
    <td><img src="https://github.com/user-attachments/assets/eee23ca1-0df5-4f24-8a89-6e0f0bf49ba4" height="300"/></td>
    <td><img src="https://github.com/user-attachments/assets/1e204fbe-abdb-4a55-8d50-2a1676f74412" height="300"/></td>
    <td><img src="https://github.com/user-attachments/assets/5852615f-00c5-44ee-a2ac-737416e612a1" height="300"/></td>
  </tr>
</table>

After taking out the silicone elastomer, it can be put directly on top of the enclosure and is ready to use! 


Author: Shyam Rajendran
