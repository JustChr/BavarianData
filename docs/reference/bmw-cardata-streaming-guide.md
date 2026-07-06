4. Streaming
In this chapter, the basics of streaming and how to use the streaming service effectively are explored. You will learn what streaming is and how it works.

Consider:

Streaming is not available for BMW Motorrad.


4.1 What is streaming?
Streaming is a method of transmitting data in real time over a network. It enables instant playback or analysis, making it ideal for applications where up-to-date information is critical. In the context of connected vehicle data, streaming provides raw, unprocessed information from your vehicle in real time.

What makes this particularly powerful is the ability to configure the stream based on your needs. It allows you to receive specific data based on predefined sets of attributes, giving you the flexibility to access only the data that’s relevant to your needs - such as location, tire pressure, or battery status.


4.2 What technology do we use?
To ensure reliable, secure, and efficient delivery of real-time vehicle data, several key technologies are used.

MQTT Protocol

Data is transmitted using the MQTT (Message Queuing Telemetry Transport) protocol – a lightweight, publish-subscribe messaging protocol designed for low-bandwidth, high-latency, or unreliable networks. MQTT is ideal for streaming vehicle data because it enables fast, efficient, and reliable communication between your system and the streaming service. Further information can be found here.

OAuth for Authentication and Authorization

Access to streaming data is protected using OAuth, an open-standard protocol that handles secure authentication and authorization. OAuth ensures that only authorized users and applications can subscribe to vehicle data streams, helping us maintain control over who accesses your data and how it's used. Please refer to this chapter concerning the technical registration, and this example of how to connect to your stream using the ID-token.

SSL/TLS for Data Security

To safeguard the data in transit, all streaming communication is encrypted using SSL/TLS (Secure Sockets Layer / Transport Layer Security) protocols. This ensures that the raw vehicle data being transmitted is protected from interception or tampering, providing end-to-end confidentiality and integrity.

These technologies work together to provide a secure, scalable, and efficient streaming experience tailored to connected vehicle environments.


4.3 How to connect to your stream
Before you can start receiving real-time data from your vehicle, a few essential setup steps are required. These ensure that your connection is properly configured, authenticated, and ready for secure data transmission.


4.3.1 Prerequisites
Mapping of the vehicle

The vehicle you want to retrieve data for needs to be mapped to your customer portal account. You need to be the PRIMARY user for the given VIN.

Generated client ID

You have successfully generated a client ID in your customer portal.

Subscription & scope

You are successfully subscribed to the CarData Streaming & your device was registered with the correct scope. This chapter contains more information about this step. Be aware, the subscription must have been performed before registering your device.

CarData
Figure 7: Subscribe to Stream

Device code flow & ID-token

To use the data stream, you need to authenticate via a valid ID token. The retrieval of the ID token is part of our Device Code Flow. Use the corresponding chapters from this integration guide and the device-code-flow swagger.
The ID token must be used as password, see this chapter for an example using the MQTTX-client.

Consider:

Only one connection per user (GCID) can be established at a time. In case there are several VINs mapped to a user account, subscribe to each VIN (represented by a topic) individually.
Once the ID-token is expired, a new connection with a valid ID-token must be initiated.
Validate dynamic scopes for your ID-token: If there are no dynamic-scopes defined, your device was not registered with the correct scopes. This chapter contains detailed information about the correct procedure.


4.3.2 Create a Streaming Configuration
To create a streaming configuration, please navigate to your customer portal.

After you have successfully subscribed your client ID to CarData streaming, the button to configure your stream becomes active.

CarData
Figure 8: Configure data stream


After clicking the button "Configure data stream", you will be forwarded to another page where all streamable attributes are listed. The credentials for your stream will be visible in the customer portal, after you have selected all the keys you want to receive data for.

CarData
Figure 9: Streaming credentials


Streaming credentials:

Host: The server address that provides the data.
Port: The network port used for establishing the connection.
Topic: The specific data stream you want to subscribe to. The topic is declared with the VIN-identifier for which you have requested the data stream.
Username: Used for identifying your client during connection, this is your GCID.
These parameters are required when setting up your MQTT client to initiate the stream.


4.4 How to connect to your stream using an MQTT client
To receive real-time data over the MQTT protocol, you'll need to set up an MQTT client – a lightweight software component that connects to an MQTT broker and subscribes to specific topics to receive messages.

Start by choosing an MQTT client. There are many options available, including MQTTX, MQTT Explorer and many others.
Verify that all prerequisites are met.
Create a streaming configuration.
Establish a Secure Connection. Set up a SSL/TLS connection to ensure that your data is encrypted in transit. Most MQTT clients support this through built-in configuration options.
Subscribe to the Topic. Once connected, subscribe to the relevant topic. This is the channel through which data will be sent. Messages published to this topic by the server will be delivered directly to your client. You can create a subscription by using your username and topic such as “username/topic”
Receive and Process Data. As data begins to stream in, your MQTT client will receive messages in real time. You can then process, display, or store this data according to your needs.
Stay authenticated. When the ID token expires, your stream connection is closed, and you cannot retrieve further vehicle data. For a continuous dataflow, request a new ID token in time and re-establish the connection.


4.5 Example: Connect to your stream using the MQTTX client and your ID token
Here´s an example of how to connect to your stream with the MQTTX-client which is free to download via this page.
In the given example, version v1.12.0 of the MQTTX-client was used.

Open the MQTTX application and create a new connection

CarData
Figure 10: MQTTX Example - New connection

The following window is displayed, enter the values accordingly

CarData
Figure 11: MQTTX Example - Connection details & credentials


In the field “Name”, select any name for your connection
In the field "Host", select the "mqtts://". For the URI select the host from the streaming credentials visible in the customer portal after the stream was configured
In the field "Port", select the port from the streaming credentials visible in the customer portal after the stream was configured
In the field "Client ID" you can keep the suggestions , as this is generated by the client.
In the field "Username", select the username from the streaming credentials visible in the customer portal after the stream was configured. This is your gcid.
In the field "Password", add your current ID-Token, which you have received via the DCF. Be aware: The token must have the correct scopes and must not yet be expired.
The other attributes (SSL/TLS, SSL Secure, and the certificates) should be set as the screenshots states.

After connecting, click on “New Subscription” to subscribe to your stream:

CarData
Figure 12: MQTTX Example - Subscription details


Add the following values from your [streaming credentials](#Streaming_credentials)

CarData
Figure 13: MQTTX Example - New subscription


As a topic name you can write: your-user-name/your-unique-topic to subscribe to a stream for the specified VIN, or you can subscribe to all your VINs with: your-user-name/+.

With this final step your stream in the MQTTX client is configured. Once data for the attributes in your streaming configuration is updated, a message in .json structure is visible in your MQTTX client.