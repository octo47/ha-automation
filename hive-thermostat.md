## Pairing

https://community.home-assistant.io/t/can-i-integrate-hive-active-heating-into-ha-without-the-hive-hub/246728/17

Writing up the procedure for others’ benefit

Remove the thermostat from the wall and remove a battery
Turn boiler off, then on again - this should cut power to the boiler receiver
Hold down central heating button on the boiler receiver until light turns pink then release
Hold down the central heating button again until the light turns amber with double flashing then release
Pair (the boiler receiver) with Home Assistant - I will be using ZHA’s ‘add device’ function
At this point the amber double flash may change to a single flash
Stop ZHA from searching for devices by pressing back
Replace the battery in the thermostat and allow to boot
Press and hold the menu and back buttons, a countdown should appear on the screen, allow the countdown to finish and release when you see ‘welcome’. After selecting a language, it will enter pairing mode.
On ZHA (or similar), select the boiler device you added earlier, now click “ADD DEVICES VIA THIS DEVICE” - the Thermostat must be connected to the ZigBee network via the boiler receiver we added earlier
The thermostat should now pair to the boiler receiver. The amber light on the boiler receiver should turn green.
