# Which cars work

> 🇩🇪 [Deutsch](DE-Supported-Cars)

BavarianData has no model list and no model-specific code. It works with
whatever BMW CarData delivers for your car, so the question is really whether
**your car has CarData**, and how much it sends.

## The quick check

Open your car in the BMW or MINI portal (**My Vehicles → Vehicle overview**, see
[step 1](Getting-Started-1-BMW-Portal-Setup#open-the-cardata-portal)). If there is a
**BMW CarData** or **Mini CarData** tile, BavarianData works with that car.

The one exception is **BMW Motorrad**: motorcycles are listed in CarData, but BMW
streams no data for them ([more](Troubleshooting-and-FAQ#does-bavariandata-work-with-a-bmw-motorcycle)).

## What you get, by drivetrain

The integration works out the drivetrain from what the car reports and adapts
the [dashboard card](The-Dashboard-Card) and the derived sensors to it.

| Drivetrain | What works |
| --- | --- |
| **Electric** | Everything: live state of charge and range, [charging history and cost](Feature-Charging-History-and-Cost), [battery health](Feature-Battery-Health), [efficiency and real range](Feature-Efficiency-and-Range), the [evcc bridge](Feature-evcc-and-Wallbox-Bridge), [trips](Feature-Trips). |
| **Plug-in hybrid** | All of the above except consumption, real range and charging cost per 100 km, which the odometer can't support on a car that also drives on fuel ([why](Feature-Efficiency-and-Range#plug-in-hybrids)). Adds tank level and combined range. |
| **Petrol / diesel** | Tank level, range, odometer, doors, windows and locks, tire pressure, service and Check Control messages, location and trips. |

## Cars owners have confirmed

These are the models people have reported running, in issues and discussions.
Any car with CarData should work; this list is only what has been seen.

| Car | Drivetrain | Worth knowing |
| --- | --- | --- |
| BMW i5 eDrive40 | Electric | The maintainer's car. Every feature is developed and tested on it. |
| BMW i4 M60 xDrive, i4 eDrive35 | Electric | Versions before 0.9.15 could mistake an i4 for a hybrid ([#53](https://github.com/JustChr/BavarianData/issues/53)). |
| BMW iX3 (new generation) | Electric | BMW streams a repeating 100 % charge target on top of the real setting ([#52](https://github.com/JustChr/BavarianData/issues/52)). |
| BMW i3s 120 Ah | Electric | — |
| BMW X3 30e xDrive | Plug-in hybrid | Doesn't stream the state of charge; it arrives with the [daily REST refresh](Feature-API-Quota#the-daily-refresh). |
| BMW X3 M40d (G01) | Diesel | — |
| BMW M240i xDrive (G42) | Petrol | Tire pressure drifted after restarts before 0.9.7 ([#7](https://github.com/JustChr/BavarianData/issues/7)). |
| BMW 530d Touring (G31, iDrive 6) | Diesel | Sends mileage, fuel and position only when a trip ends, so the trip journal stays empty ([more](Feature-Trips#older-cars-that-report-only-at-the-end-of-a-trip)). |
| MINI (petrol) | Petrol | Set up through the MINI portal. Sends fewer fields than a BMW, for example no lifetime fuel-consumption counters. |

## What differs from car to car

- **Which fields arrive.** BMW's catalogue has [hundreds of descriptors](Reference),
  and no car sends them all. Run
  [`get_coverage_report`](Services-Reference#get_coverage_report) to see which of
  the ones you selected your car actually delivers.
- **How often.** Some cars send a value only when it changes, others repeat
  everything every half minute while awake. A parked car can be quiet for hours;
  that is normal.
- **During a drive.** Most cars stream position and mileage while driving, which
  is what the [trip journal](Feature-Trips) is built from. Some older ones only
  report at the end.

## Your car isn't listed?

It almost certainly works anyway. Once it runs, say so in
[Discussions](https://github.com/JustChr/BavarianData/discussions) with the model
and drivetrain, so the next owner of that car finds it here. If something looks
wrong, open an [issue](https://github.com/JustChr/BavarianData/issues) and attach a
[diagnostics download](Troubleshooting-and-FAQ#download-diagnostics): it shows
what your car sends.
