#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>
#include <TinyGPSPlus.h>
#include <Wire.h>
#include <SensirionI2cScd4x.h>
#include <SPI.h>
#include <SD.h>

// -------------------- Wi-Fi --------------------

const char* ssid = "TP-Link_7848";
const char* password = "90706963";

//const char* ssid = "Bleh1234";
//const char* password = "bleh1234";

const char* serverUrl = "https://servidor-metano-uav.onrender.com/dados";
const char* commandUrl = "https://servidor-metano-uav.onrender.com/api/command";

// -------------------- NTP --------------------

const char* ntpServer = "pool.ntp.org";

const long gmtOffset_sec = 3600;
const int daylightOffset_sec = 0;

// -------------------- GPS --------------------

// GPS TX -> ESP32 RX / GPIO17
// GPS RX -> ESP32 TX / GPIO16
#define GPS_RX_PIN 17
#define GPS_TX_PIN 16

HardwareSerial gpsSerial(1);
TinyGPSPlus gps;

// -------------------- SCD40 --------------------

// SCD40 SDA -> GPIO4
// SCD40 SCL -> GPIO5
#define SCD40_SDA_PIN 4
#define SCD40_SCL_PIN 5

SensirionI2cScd4x scd4x;

static char scd40ErrorMessage[256];
static int16_t scd40Error;

// -------------------- MQ-4 --------------------

// MQ-4 S -> divisor de tensão -> GPIO6
#define MQ4_PIN 6

const float ADC_REF_VOLTAGE = 3.3;
const float ADC_MAX_VALUE = 4095.0;

// Divisor de tensão 10k + 10k.
// O ESP32 lê metade da tensão do sensor, por isso multiplicar por 2.
const float MQ4_DIVIDER_FACTOR = 2.0;

// Calibração automática do MQ-4
const unsigned long MQ4_CALIBRATION_DURATION_MS = 15UL * 60UL * 1000UL;  // 15 minutos

// Guarda as últimas 12 leituras.
// A calibração automática usa as últimas 12.
// O botão "Começar teste agora" usa as últimas 6.
const int MQ4_BASELINE_SAMPLE_COUNT = 12;
const int MQ4_MIN_BASELINE_SAMPLES = 12;
const int MQ4_FORCE_BASELINE_SAMPLE_COUNT = 6;

// 100% = baseline + 400 raw.
const int MQ4_RELATIVE_RANGE_RAW = 400;

int mq4BaselineSamples[MQ4_BASELINE_SAMPLE_COUNT];
int mq4BaselineIndex = 0;
int mq4BaselineCount = 0;

bool mq4Calibrated = false;
int mq4BaselineRaw = 0;
unsigned long mq4CalibrationStartMillis = 0;

// -------------------- MicroSD --------------------

// MicroSD VCC  -> 3V3
// MicroSD GND  -> GND
// MicroSD CS   -> GPIO10
// MicroSD MOSI -> GPIO11
// MicroSD CLK  -> GPIO12
// MicroSD MISO -> GPIO13

#define SD_CS_PIN 10
#define SD_MOSI_PIN 11
#define SD_SCK_PIN 12
#define SD_MISO_PIN 13

SPIClass spiSD(FSPI);

bool sdOk = false;
const char* SD_FILENAME = "/dados_uav.csv";

// -------------------- Envio --------------------

unsigned long lastSend = 0;
const unsigned long sendInterval = 5000;  // 5 segundos

String testId = "";

// -------------------- Funções de tempo --------------------

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

// -------------------- Ler MQ-4 --------------------

int readMQ4Average() {
  long sum = 0;
  const int samples = 50;

  for (int i = 0; i < samples; i++) {
    sum += analogRead(MQ4_PIN);
    delay(2);
  }

  return sum / samples;
}

// -------------------- Calibração MQ-4 --------------------

void addMQ4CalibrationSample(int mq4Raw) {
  mq4BaselineSamples[mq4BaselineIndex] = mq4Raw;

  mq4BaselineIndex++;

  if (mq4BaselineIndex >= MQ4_BASELINE_SAMPLE_COUNT) {
    mq4BaselineIndex = 0;
  }

  if (mq4BaselineCount < MQ4_BASELINE_SAMPLE_COUNT) {
    mq4BaselineCount++;
  }
}

int calculateMQ4BaselineAverage() {
  if (mq4BaselineCount <= 0) {
    return 0;
  }

  long sum = 0;

  for (int i = 0; i < mq4BaselineCount; i++) {
    sum += mq4BaselineSamples[i];
  }

  return sum / mq4BaselineCount;
}

int calculateMQ4LastSamplesAverage(int sampleCount) {
  if (sampleCount <= 0) {
    return 0;
  }

  if (sampleCount > MQ4_BASELINE_SAMPLE_COUNT) {
    sampleCount = MQ4_BASELINE_SAMPLE_COUNT;
  }

  if (mq4BaselineCount < sampleCount) {
    return 0;
  }

  long sum = 0;

  for (int i = 0; i < sampleCount; i++) {
    int index = mq4BaselineIndex - 1 - i;

    while (index < 0) {
      index += MQ4_BASELINE_SAMPLE_COUNT;
    }

    sum += mq4BaselineSamples[index];
  }

  return sum / sampleCount;
}

unsigned long getMQ4CalibrationRemainingSeconds() {
  if (mq4Calibrated) {
    return 0;
  }

  unsigned long elapsed = millis() - mq4CalibrationStartMillis;

  if (elapsed >= MQ4_CALIBRATION_DURATION_MS) {
    return 0;
  }

  return (MQ4_CALIBRATION_DURATION_MS - elapsed) / 1000UL;
}

void forceMQ4BaselineFromLastSamples(int sampleCount) {
  if (sampleCount <= 0) {
    sampleCount = MQ4_FORCE_BASELINE_SAMPLE_COUNT;
  }

  if (sampleCount > MQ4_BASELINE_SAMPLE_COUNT) {
    sampleCount = MQ4_BASELINE_SAMPLE_COUNT;
  }

  if (mq4BaselineCount < sampleCount) {
    Serial.println();
    Serial.println("=================================");
    Serial.println("Comando recebido: começar teste agora.");
    Serial.println("Mas ainda não há amostras suficientes para calcular baseline.");
    Serial.print("Amostras atuais: ");
    Serial.print(mq4BaselineCount);
    Serial.print("/");
    Serial.println(sampleCount);
    Serial.println("A calibração continua.");
    Serial.println("=================================");
    return;
  }

  mq4BaselineRaw = calculateMQ4LastSamplesAverage(sampleCount);
  mq4Calibrated = true;

  Serial.println();
  Serial.println("=================================");
  Serial.println("Baseline MQ-4 forçado pelo servidor.");
  Serial.print("Amostras usadas: ");
  Serial.println(sampleCount);
  Serial.print("Baseline MQ-4 definido: ");
  Serial.println(mq4BaselineRaw);
  Serial.println("Modo de medição iniciado imediatamente.");
  Serial.println("=================================");
}

void updateMQ4Calibration(int mq4Raw) {
  // Guarda sempre os últimos valores, mesmo depois de calibrado.
  // Isto permite recalcular baseline através do botão do servidor.
  addMQ4CalibrationSample(mq4Raw);

  if (mq4Calibrated) {
    return;
  }

  unsigned long elapsed = millis() - mq4CalibrationStartMillis;

  if (
    elapsed >= MQ4_CALIBRATION_DURATION_MS &&
    mq4BaselineCount >= MQ4_MIN_BASELINE_SAMPLES
  ) {
    mq4BaselineRaw = calculateMQ4BaselineAverage();
    mq4Calibrated = true;

    Serial.println();
    Serial.println("=================================");
    Serial.println("Calibração MQ-4 concluída.");
    Serial.print("Baseline MQ-4 definido: ");
    Serial.println(mq4BaselineRaw);
    Serial.println("Modo de medição iniciado.");
    Serial.println("=================================");
  }
}

float calculateMQ4RelativePercent(int mq4Raw) {
  if (!mq4Calibrated || mq4BaselineRaw <= 0) {
    return 0.0;
  }

  float percent = ((float)(mq4Raw - mq4BaselineRaw) / (float)MQ4_RELATIVE_RANGE_RAW) * 100.0;

  if (percent < 0.0) {
    percent = 0.0;
  }

  if (percent > 100.0) {
    percent = 100.0;
  }

  return percent;
}

String getSystemPhase() {
  if (mq4Calibrated) {
    return "measuring";
  }

  return "calibrating";
}

// -------------------- Comandos do servidor --------------------

int parseJsonInt(String payload, String key, int defaultValue) {
  String search = "\"" + key + "\":";
  int start = payload.indexOf(search);

  if (start < 0) {
    return defaultValue;
  }

  start += search.length();

  while (start < payload.length()) {
    char c = payload.charAt(start);

    if (c == ' ' || c == '"') {
      start++;
    } else {
      break;
    }
  }

  int end = start;

  while (end < payload.length()) {
    char c = payload.charAt(end);

    if ((c >= '0' && c <= '9') || c == '-') {
      end++;
    } else {
      break;
    }
  }

  if (end <= start) {
    return defaultValue;
  }

  return payload.substring(start, end).toInt();
}

void checkServerCommand() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  HTTPClient http;

  http.setTimeout(3000);
  http.begin(commandUrl);

  int httpResponseCode = http.GET();

  if (httpResponseCode == 200) {
    String payload = http.getString();

    if (payload.indexOf("\"command\":\"force_baseline\"") >= 0 ||
        payload.indexOf("\"command\": \"force_baseline\"") >= 0) {
      int sampleCount = parseJsonInt(payload, "sample_count", MQ4_FORCE_BASELINE_SAMPLE_COUNT);

      Serial.println();
      Serial.print("Comando do servidor recebido: ");
      Serial.println(payload);

      forceMQ4BaselineFromLastSamples(sampleCount);
    }
  } else if (httpResponseCode > 0) {
    Serial.print("Resposta /api/command: ");
    Serial.println(httpResponseCode);
  } else {
    Serial.print("Erro ao consultar /api/command: ");
    Serial.print(httpResponseCode);
    Serial.print(" - ");
    Serial.println(http.errorToString(httpResponseCode));
  }

  http.end();
}

// -------------------- Ler SCD40 --------------------

bool readSCD40(uint16_t &co2, float &temperature, float &humidity) {
  bool dataReady = false;

  co2 = 0;
  temperature = 0.0;
  humidity = 0.0;

  scd40Error = scd4x.getDataReadyStatus(dataReady);

  if (scd40Error) {
    Serial.print("Erro getDataReadyStatus SCD40: ");
    errorToString(scd40Error, scd40ErrorMessage, sizeof(scd40ErrorMessage));
    Serial.println(scd40ErrorMessage);
    return false;
  }

  if (!dataReady) {
    Serial.println("SCD40 ainda sem dados prontos.");
    return false;
  }

  scd40Error = scd4x.readMeasurement(co2, temperature, humidity);

  if (scd40Error) {
    Serial.print("Erro readMeasurement SCD40: ");
    errorToString(scd40Error, scd40ErrorMessage, sizeof(scd40ErrorMessage));
    Serial.println(scd40ErrorMessage);
    return false;
  }

  if (co2 == 0) {
    Serial.println("Medição SCD40 inválida.");
    return false;
  }

  return true;
}

// -------------------- Inicializar microSD --------------------

void initSD() {
  Serial.println();
  Serial.println("A iniciar cartão microSD...");

  spiSD.begin(SD_SCK_PIN, SD_MISO_PIN, SD_MOSI_PIN, SD_CS_PIN);

  if (!SD.begin(SD_CS_PIN, spiSD)) {
    Serial.println("Erro: cartão microSD não inicializou.");
    Serial.println("O sistema continua sem gravação local.");
    sdOk = false;
    return;
  }

  sdOk = true;

  Serial.println("Cartão microSD inicializado com sucesso.");

  if (!SD.exists(SD_FILENAME)) {
    File file = SD.open(SD_FILENAME, FILE_WRITE);

    if (!file) {
      Serial.println("Erro ao criar ficheiro CSV no microSD.");
      sdOk = false;
      return;
    }

    file.println(
      "timestamp;"
      "latitude;"
      "longitude;"
      "gps_fix;"
      "gps_satelites;"
      "gps_hdop;"
      "gps_age_ms;"
      "co2_ppm;"
      "temperatura_c;"
      "humidade_percent;"
      "mq4_raw;"
      "mq4_voltage_adc;"
      "mq4_voltage_sensor;"
      "mq4_baseline_raw;"
      "mq4_delta;"
      "mq4_percent;"
      "mq4_relative_percent;"
      "mq4_calibrated;"
      "system_phase;"
      "test_id"
    );

    file.close();

    Serial.println("Ficheiro CSV criado com cabeçalho.");
  } else {
    Serial.println("Ficheiro CSV já existe. Dados serão acrescentados.");
  }
}

// -------------------- Gravar no microSD --------------------

void writeToSD(
  String timestamp,
  double latitude,
  double longitude,
  bool gpsFix,
  int gpsSatellites,
  float gpsHdop,
  unsigned long gpsAge,

  bool scd40Ok,
  uint16_t co2_ppm,
  float temperatura_c,
  float humidade_percent,

  int mq4Raw,
  float mq4AdcVoltage,
  float mq4SensorVoltage,
  bool mq4CalibratedNow,
  int mq4BaselineRawNow,
  int mq4Delta,
  float mq4RelativePercent,
  String systemPhase
) {
  if (!sdOk) {
    Serial.println("microSD não disponível. Linha não gravada.");
    return;
  }

  File file = SD.open(SD_FILENAME, FILE_APPEND);

  if (!file) {
    Serial.println("Erro ao abrir ficheiro CSV para escrita.");
    return;
  }

  file.print(timestamp);
  file.print(";");

  if (gpsFix) {
    file.print(latitude, 6);
  }
  file.print(";");

  if (gpsFix) {
    file.print(longitude, 6);
  }
  file.print(";");

  file.print(gpsFix ? 1 : 0);
  file.print(";");

  file.print(gpsSatellites);
  file.print(";");

  if (gpsHdop >= 0) {
    file.print(gpsHdop, 2);
  }
  file.print(";");

  if (gpsAge > 0 && gpsAge < 600000) {
    file.print(gpsAge);
  }
  file.print(";");

  if (scd40Ok) {
    file.print(co2_ppm);
  }
  file.print(";");

  if (scd40Ok) {
    file.print(temperatura_c, 2);
  }
  file.print(";");

  if (scd40Ok) {
    file.print(humidade_percent, 2);
  }
  file.print(";");

  file.print(mq4Raw);
  file.print(";");

  file.print(mq4AdcVoltage, 3);
  file.print(";");

  file.print(mq4SensorVoltage, 3);
  file.print(";");

  if (mq4CalibratedNow) {
    file.print(mq4BaselineRawNow);
  }
  file.print(";");

  if (mq4CalibratedNow) {
    file.print(mq4Delta);
  }
  file.print(";");

  if (mq4CalibratedNow) {
    file.print(mq4RelativePercent, 2);
  }
  file.print(";");

  if (mq4CalibratedNow) {
    file.print(mq4RelativePercent, 2);
  }
  file.print(";");

  file.print(mq4CalibratedNow ? 1 : 0);
  file.print(";");

  file.print(systemPhase);
  file.print(";");

  file.println(testId);

  file.close();

  Serial.println("Linha gravada no microSD.");
}

// -------------------- Envio para servidor --------------------

void sendToCloud(
  String timestamp,
  double latitude,
  double longitude,
  bool gpsFix,
  int gpsSatellites,
  float gpsHdop,
  unsigned long gpsAge,

  float ndir_ppm,

  bool scd40Ok,
  uint16_t co2_ppm,
  float temperatura_c,
  float humidade_percent,

  int mq4Raw,
  float mq4AdcVoltage,
  float mq4SensorVoltage,
  bool mq4CalibratedNow,
  int mq4BaselineRawNow,
  int mq4Delta,
  float mq4RelativePercent,
  String systemPhase
) {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;

    http.setTimeout(5000);
    http.begin(serverUrl);
    http.addHeader("Content-Type", "application/json");

    String json = "{";

    json += "\"timestamp\":\"" + timestamp + "\",";

    json += "\"latitude\":";
    if (gpsFix) {
      json += String(latitude, 6);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"longitude\":";
    if (gpsFix) {
      json += String(longitude, 6);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"ndir_ppm\":" + String(ndir_ppm, 2) + ",";

    json += "\"gps_fix\":" + String(gpsFix ? 1 : 0) + ",";
    json += "\"gps_satelites\":" + String(gpsSatellites) + ",";

    json += "\"gps_hdop\":";
    if (gpsHdop >= 0) {
      json += String(gpsHdop, 2);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"gps_age_ms\":";
    if (gpsAge > 0 && gpsAge < 600000) {
      json += String(gpsAge);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"co2_ppm\":";
    if (scd40Ok) {
      json += String(co2_ppm);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"temperatura_c\":";
    if (scd40Ok) {
      json += String(temperatura_c, 2);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"humidade_percent\":";
    if (scd40Ok) {
      json += String(humidade_percent, 2);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"mq4_raw\":" + String(mq4Raw) + ",";
    json += "\"mq4_voltage_adc\":" + String(mq4AdcVoltage, 3) + ",";
    json += "\"mq4_voltage_sensor\":" + String(mq4SensorVoltage, 3) + ",";

    json += "\"mq4_baseline_raw\":";
    if (mq4CalibratedNow) {
      json += String(mq4BaselineRawNow);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"mq4_delta\":";
    if (mq4CalibratedNow) {
      json += String(mq4Delta);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"mq4_percent\":";
    if (mq4CalibratedNow) {
      json += String(mq4RelativePercent, 2);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"mq4_relative_percent\":";
    if (mq4CalibratedNow) {
      json += String(mq4RelativePercent, 2);
    } else {
      json += "null";
    }
    json += ",";

    json += "\"mq4_calibrated\":" + String(mq4CalibratedNow ? 1 : 0) + ",";
    json += "\"system_phase\":\"" + systemPhase + "\",";

    json += "\"mq4_calibration_remaining_seconds\":";
    if (!mq4CalibratedNow) {
      json += String(getMQ4CalibrationRemainingSeconds());
    } else {
      json += "0";
    }
    json += ",";

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

    Serial.println();
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

// -------------------- Setup --------------------

void setup() {
  Serial.begin(115200);
  delay(1000);

  randomSeed(analogRead(0));

  // MQ-4 ADC
  analogReadResolution(12);
  analogSetPinAttenuation(MQ4_PIN, ADC_11db);

  Serial.println();
  Serial.println("MQ-4 ADC iniciado.");

  // GPS UART
  gpsSerial.begin(9600, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  Serial.println();
  Serial.println("GPS iniciado a 9600 baud");
  Serial.println("A aguardar dados do GPS...");

  // SCD40 I2C
  Wire.begin(SCD40_SDA_PIN, SCD40_SCL_PIN);
  Wire.setClock(100000);

  scd4x.begin(Wire, SCD41_I2C_ADDR_62);

  delay(30);

  scd40Error = scd4x.wakeUp();

  scd40Error = scd4x.stopPeriodicMeasurement();
  delay(500);

  scd40Error = scd4x.reinit();

  scd40Error = scd4x.startPeriodicMeasurement();

  if (scd40Error) {
    Serial.print("Erro ao iniciar SCD40: ");
    errorToString(scd40Error, scd40ErrorMessage, sizeof(scd40ErrorMessage));
    Serial.println(scd40ErrorMessage);
  } else {
    Serial.println("SCD40 iniciado com sucesso.");
  }

  // microSD
  initSD();

  // Wi-Fi
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

  // NTP
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

  mq4CalibrationStartMillis = millis();

  Serial.println();
  Serial.println("Calibração automática do MQ-4 iniciada.");
  Serial.println("Mantém o sistema em ar limpo durante a calibração.");
  Serial.print("Duração da calibração: ");
  Serial.print(MQ4_CALIBRATION_DURATION_MS / 60000UL);
  Serial.println(" minutos.");

  Serial.println();
  Serial.println("Botão do servidor disponível:");
  Serial.println("Começar teste agora = média dos últimos 6 valores MQ-4");
}

// -------------------- Loop --------------------

void loop() {
  // Ler continuamente dados do GPS
  while (gpsSerial.available() > 0) {
    gps.encode(gpsSerial.read());
  }

  if (millis() - lastSend >= sendInterval) {
    lastSend = millis();

    String timestamp = getTimestamp();

    double latitude = 0.0;
    double longitude = 0.0;

    int gpsSatellites = 0;
    float gpsHdop = -1;
    unsigned long gpsAge = 0;

    if (gps.satellites.isValid()) {
      gpsSatellites = gps.satellites.value();
    }

    if (gps.hdop.isValid()) {
      gpsHdop = gps.hdop.hdop();
    }

    if (gps.location.isValid()) {
      gpsAge = gps.location.age();
    }

    bool gpsFix = false;

    if (
      gps.location.isValid() &&
      gps.location.age() < 10000 &&
      gpsSatellites >= 4
    ) {
      gpsFix = true;
      latitude = gps.location.lat();
      longitude = gps.location.lng();
    }

    Serial.println();

    if (gpsFix) {
      Serial.println("GPS FIX OK");

      Serial.print("Latitude: ");
      Serial.println(latitude, 6);

      Serial.print("Longitude: ");
      Serial.println(longitude, 6);
    } else {
      Serial.println("Sem fix GPS fiavel ainda");
    }

    Serial.print("Satélites: ");
    Serial.println(gpsSatellites);

    Serial.print("HDOP: ");
    if (gpsHdop >= 0) {
      Serial.println(gpsHdop, 2);
    } else {
      Serial.println("sem valor");
    }

    Serial.print("Idade da posição GPS ms: ");
    if (gps.location.isValid()) {
      Serial.println(gpsAge);
    } else {
      Serial.println("sem posição válida");
    }

    Serial.print("Caracteres GPS processados: ");
    Serial.println(gps.charsProcessed());

    Serial.print("Frases GPS com fix: ");
    Serial.println(gps.sentencesWithFix());

    Serial.print("Erros checksum GPS: ");
    Serial.println(gps.failedChecksum());

    // Ler SCD40
    uint16_t co2_ppm = 0;
    float temperatura_c = 0.0;
    float humidade_percent = 0.0;

    bool scd40Ok = readSCD40(co2_ppm, temperatura_c, humidade_percent);

    if (scd40Ok) {
      Serial.print("CO2: ");
      Serial.print(co2_ppm);
      Serial.println(" ppm");

      Serial.print("Temperatura SCD40: ");
      Serial.print(temperatura_c, 2);
      Serial.println(" C");

      Serial.print("Humidade SCD40: ");
      Serial.print(humidade_percent, 2);
      Serial.println(" %");
    } else {
      Serial.println("SCD40 sem leitura válida.");
    }

    // Ler MQ-4
    int mq4Raw = readMQ4Average();

    updateMQ4Calibration(mq4Raw);

    // Depois de guardar a leitura atual, pergunta ao servidor se há comando.
    // Se houver "force_baseline", usa a média dos últimos 6 valores.
    checkServerCommand();

    float mq4AdcVoltage = (mq4Raw / ADC_MAX_VALUE) * ADC_REF_VOLTAGE;
    float mq4SensorVoltage = mq4AdcVoltage * MQ4_DIVIDER_FACTOR;

    int mq4Delta = 0;
    float mq4RelativePercent = 0.0;

    if (mq4Calibrated) {
      mq4Delta = mq4Raw - mq4BaselineRaw;
      mq4RelativePercent = calculateMQ4RelativePercent(mq4Raw);
    }

    String systemPhase = getSystemPhase();

    Serial.print("Fase do sistema: ");
    Serial.println(systemPhase);

    if (!mq4Calibrated) {
      Serial.print("Tempo restante calibração MQ-4: ");
      Serial.print(getMQ4CalibrationRemainingSeconds());
      Serial.println(" s");

      Serial.print("Amostras de baseline guardadas: ");
      Serial.print(mq4BaselineCount);
      Serial.print("/");
      Serial.println(MQ4_BASELINE_SAMPLE_COUNT);
    } else {
      Serial.print("Baseline MQ-4: ");
      Serial.println(mq4BaselineRaw);
    }

    Serial.print("MQ-4 raw: ");
    Serial.println(mq4Raw);

    Serial.print("MQ-4 tensão ADC: ");
    Serial.print(mq4AdcVoltage, 3);
    Serial.println(" V");

    Serial.print("MQ-4 tensão sensor estimada: ");
    Serial.print(mq4SensorVoltage, 3);
    Serial.println(" V");

    if (mq4Calibrated) {
      Serial.print("MQ-4 delta face ao baseline: ");
      Serial.println(mq4Delta);

      Serial.print("Metano relativo MQ-4: ");
      Serial.print(mq4RelativePercent, 2);
      Serial.println(" %");
    } else {
      Serial.println("Metano relativo MQ-4: ainda em calibração");
    }

    // Gravar localmente primeiro
    writeToSD(
      timestamp,
      latitude,
      longitude,
      gpsFix,
      gpsSatellites,
      gpsHdop,
      gpsAge,

      scd40Ok,
      co2_ppm,
      temperatura_c,
      humidade_percent,

      mq4Raw,
      mq4AdcVoltage,
      mq4SensorVoltage,
      mq4Calibrated,
      mq4BaselineRaw,
      mq4Delta,
      mq4RelativePercent,
      systemPhase
    );

    // Mantido para compatibilidade com o servidor atual.
    float ndir_ppm = mq4Raw;

    // Enviar para o servidor
    sendToCloud(
      timestamp,
      latitude,
      longitude,
      gpsFix,
      gpsSatellites,
      gpsHdop,
      gpsAge,

      ndir_ppm,

      scd40Ok,
      co2_ppm,
      temperatura_c,
      humidade_percent,

      mq4Raw,
      mq4AdcVoltage,
      mq4SensorVoltage,
      mq4Calibrated,
      mq4BaselineRaw,
      mq4Delta,
      mq4RelativePercent,
      systemPhase
    );
  }
}