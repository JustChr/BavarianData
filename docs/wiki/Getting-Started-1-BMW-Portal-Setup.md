# 1. Set up BMW CarData in the portal

Do this **before** adding the integration to Home Assistant.

BMW CarData is BMW's own telematics service. To use it you need a **client ID**
generated in the BMW portal, with two scopes authorized. The CarData portal
isn't offered in every market, but the client ID it produces is **account-wide**,
so you can complete this setup from any supported region and use it everywhere,
on every vehicle on the account.

## Requirements

- A BMW account with a vehicle that supports CarData.
- **CarData API** and **CarData Streaming** subscribed in the BMW portal.

It helps to skim
[BMW's CarData documentation](https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Introduction)
once before starting — the steps below mirror it.

## Open the CarData portal

Sign in to your BMW account and open **My Vehicles → Vehicle overview**:

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-account.png" alt="MyBMW account landing page; under 'My Vehicles' the 'Vehicle overview' link is highlighted" width="760" />
</p>

Direct links to the vehicle overview per market:

|       | English | German | Austrian |
| ----- | ------- | ------ | -------- |
| BMW   | [vehicle overview](https://www.bmw.co.uk/en-gb/mybmw/vehicle-overview) | [Fahrzeugübersicht](https://www.bmw.de/de-de/mybmw/vehicle-overview) | [Fahrzeugübersicht](https://www.bmw.at/de-at/mybmw/vehicle-overview) |
| Mini  | [vehicle overview](https://www.mini.co.uk/en-gb/mymini/vehicle-overview) | [Fahrzeugübersicht](https://www.mini.de/de-de/mymini/vehicle-overview) | [Fahrzeugübersicht](https://www.mini.at/de-at/mymini/vehicle-overview) |

## Steps

1. Select your vehicle and open the **BMW CarData** / **Mini CarData** tile.

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-vehicle-overview.png" alt="Vehicle overview page for a BMW i5 eDrive40 with the 'BMW CarData' tile among the vehicle tiles" width="760" />
   </p>

2. Press **Create CarData Client** to generate a client ID:

   <p align="center">
     <img src="https://cd-prd-euc1-cardata-public-s3.s3.eu-central-1.amazonaws.com/customer-portal/b2c/2_1_Create-Client.png" alt="'Technical access to BMW CarData' screen with the 'Create CarData Client' button highlighted" width="760" />
   </p>

3. Give the client **both** scopes — `cardata:api:read` and
   `cardata:streaming:read` — and authorize it. On the **Technical access to
   BMW CarData** screen you should end up with a **Client ID** shown and both
   **Request access to CarData API** and **CarData Stream** switched on.

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bmw-portal-cardata-client.png" alt="'Technical access to BMW CarData' screen showing the generated Client ID (redacted) with the CarData API and CarData Stream toggles both enabled, plus Delete Client and Authenticate device buttons" width="760" />
   </p>

   > If the portal throws a scope error, reload the page, add one scope, wait
   > ~30 seconds, then add the second.

That's all you need in the portal for now.

> **Don't tick anything under Data Selection yet.** Which descriptors to stream
> is chosen from inside Home Assistant after install
> ([step 4](Getting-Started-4-Choose-Data)). The **guided** setup turns them on
> for you with a one-click **Activate BMW data** bookmarklet; the **manual** setup
> hands you a portal snippet for exactly the clusters you pick. Either way, doing
> it by hand here means picking through hundreds of technical fields — so leave it.

That's the whole portal setup. In [step 3](Getting-Started-3-Add-and-Authorize)
you'll either let the **guided** path discover this client ID for you, or paste it
in yourself on the **manual** path — so keep the client ID handy if you plan to go
manual.

**Next:** [2. Install via HACS →](Getting-Started-2-Install)
