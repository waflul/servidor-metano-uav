#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>

//const char* ssid = "TP-Link_7848";
//const char* password = "90706963";

const char* ssid = "Bleh1234";
const char* password = "bleh1234";


const char* serverUrl = "https://servidor-metano-uav.onrender.com/dados";
//const char* serverUrl = "http://172.20.10.13:5000/dados";


const char* ntpServer = "pool.ntp.org";

const long gmtOffset_sec = 3600;
const int daylightOffset_sec = 0;

float latitudeBase = 41.123400;
float longitudeBase = -8.456700;

unsigned long lastSend = 0;
const unsigned long sendInterval = 5000; //5sec

String testId = "";

String getTimestamp() {
  struct tm timeinfo;

  if (!getLocalTime(&timeinfo)) {
    return "Sem hora NTP";
  }

  char buffer[25];
  strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", &timeinfo);

  return String(buffer);
}

String createTestId() {
  String id = getTimestamp();

  id.replace(" ", "_");
  id.replace(":", "-");

  return id;
}

float getSimulatedNDIR() {
  return random(500, 8000) / 100.0;
}

float getSimulatedLatitude() {
  return latitudeBase + random(-100, 100) / 1000000.0;
}

float getSimulatedLongitude() {
  return longitudeBase + random(-100, 100) / 1000000.0;
}

void sendToCloud(String timestamp, float latitude, float longitude, float ndir_ppm) {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;

    http.setTimeout(5000);
    http.begin(serverUrl);
    http.addHeader("Content-Type", "application/json");

    String json = "{";
    json += "\"timestamp\":\"" + timestamp + "\",";
    json += "\"latitude\":" + String(latitude, 6) + ",";
    json += "\"longitude\":" + String(longitude, 6) + ",";
    json += "\"ndir_ppm\":" + String(ndir_ppm, 2) + ",";
    json += "\"test_id\":\"" + testId + "\"";
    json += "}";

    int httpResponseCode = http.POST(json);

    if (httpResponseCode <= 0) {
      Serial.print("Primeira tentativa falhou: ");
      Serial.print(httpResponseCode);
      Serial.print(" - ");
      Serial.println(http.errorToString(httpResponseCode));

      delay(500);

      httpResponseCode = http.POST(json);
    }

    Serial.print("JSON enviado: ");
    Serial.println(json);

    if (httpResponseCode > 0) {
      Serial.print("Resposta HTTP: ");
      Serial.println(httpResponseCode);
    } else {
      Serial.print("Erro HTTP final: ");
      Serial.print(httpResponseCode);
      Serial.print(" - ");
      Serial.println(http.errorToString(httpResponseCode));
    }

    http.end();
  } else {
    Serial.println("Wi-Fi desligado. A tentar reconectar...");
    WiFi.reconnect();
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  randomSeed(analogRead(0));

  Serial.print("A ligar ao Wi-Fi: ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("Wi-Fi ligado");
  Serial.print("IP do ESP32: ");
  Serial.println(WiFi.localIP());

  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);

  Serial.print("A sincronizar hora NTP");
  struct tm timeinfo;

  while (!getLocalTime(&timeinfo)) {
    Serial.print(".");
    delay(500);
  }

  Serial.println();
  Serial.println("Hora sincronizada");

  testId = createTestId();

  Serial.print("Hora atual: ");
  Serial.println(getTimestamp());

  Serial.print("ID do teste: ");
  Serial.println(testId);
}

void loop() {
  if (millis() - lastSend >= sendInterval) {
    lastSend = millis();

    String timestamp = getTimestamp();
    float latitude = getSimulatedLatitude();
    float longitude = getSimulatedLongitude();
    float ndir_ppm = getSimulatedNDIR();

    sendToCloud(timestamp, latitude, longitude, ndir_ppm);
  }
}
