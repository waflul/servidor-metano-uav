from flask import Flask, request, jsonify, send_file, redirect
import csv
import os
from datetime import datetime, timedelta

app = Flask(__name__)

FILENAME = "dados_recebidos.csv"

# 12 amostras x 5 segundos = 1 minuto
SAMPLES_PER_PAGE = 12
SAMPLE_INTERVAL_SECONDS = 5

# Botão "Começar teste agora"
FORCE_BASELINE_SAMPLE_COUNT = 6

PENDING_COMMAND = {
    "command": "none",
    "command_id": 0,
    "sample_count": FORCE_BASELINE_SAMPLE_COUNT
}

LAST_CALIBRATION_REMAINING_SECONDS = None
LAST_CALIBRATION_REMAINING_RECEIVED_AT = None

HEADER = [
    "timestamp",
    "latitude",
    "longitude",
    "mq4_raw",
    "mq4_relative_percent",
    "co2_ppm",
    "temperatura_c",
    "humidade_percent",
    "gps_fix",
    "gps_satelites",
    "gps_hdop",
    "gps_age_ms",
    "mq4_voltage_adc",
    "mq4_voltage_sensor",
    "mq4_baseline_raw",
    "mq4_delta",
    "mq4_calibrated",
    "system_phase",
    "test_id"
]

IDX_TIMESTAMP = 0
IDX_LATITUDE = 1
IDX_LONGITUDE = 2
IDX_MQ4_RAW = 3
IDX_MQ4_RELATIVE_PERCENT = 4
IDX_CO2 = 5
IDX_TEMPERATURA = 6
IDX_HUMIDADE = 7
IDX_GPS_FIX = 8
IDX_GPS_SATELITES = 9
IDX_GPS_HDOP = 10
IDX_GPS_AGE = 11
IDX_MQ4_VOLTAGE_ADC = 12
IDX_MQ4_VOLTAGE_SENSOR = 13
IDX_MQ4_BASELINE_RAW = 14
IDX_MQ4_DELTA = 15
IDX_MQ4_CALIBRATED = 16
IDX_SYSTEM_PHASE = 17
IDX_TEST_ID = 18


def init_csv():
    file_exists = os.path.exists(FILENAME)
    file_empty = True

    if file_exists:
        file_empty = os.path.getsize(FILENAME) == 0

    if not file_exists or file_empty:
        with open(FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(HEADER)


def format_float(value, decimals):
    if value is None:
        return ""

    try:
        return f"{float(value):.{decimals}f}"
    except:
        return ""


def format_int(value):
    if value is None:
        return ""

    try:
        return str(int(value))
    except:
        return ""


def get_cell(row, index):
    if len(row) > index:
        return row[index]

    return ""


def parse_timestamp(timestamp):
    if not timestamp:
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%H:%M:%S"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(timestamp, fmt)
        except:
            pass

    return None


def parse_float_cell(value):
    try:
        if value is None or value == "":
            return None

        return float(str(value).replace(",", "."))
    except:
        return None


def get_row_test_id(row):
    if len(row) > IDX_TEST_ID and get_cell(row, IDX_TEST_ID).strip():
        return get_cell(row, IDX_TEST_ID)

    if len(row) >= 14 and row[13].strip():
        return row[13]

    if len(row) >= 5 and row[4].strip():
        return row[4]

    return None


def row_is_measuring(row):
    phase = get_cell(row, IDX_SYSTEM_PHASE).strip().lower()

    if phase:
        return phase == "measuring"

    return True


def get_measuring_rows(rows):
    return [row for row in rows if row_is_measuring(row)]


def get_latest_system_phase(rows):
    for row in reversed(rows):
        phase = get_cell(row, IDX_SYSTEM_PHASE).strip()

        if phase:
            return phase

    return "-"


def get_latest_baseline(rows):
    for row in reversed(rows):
        baseline = get_cell(row, IDX_MQ4_BASELINE_RAW).strip()

        if baseline:
            return baseline

    return "-"


def update_latest_calibration_remaining(data):
    global LAST_CALIBRATION_REMAINING_SECONDS
    global LAST_CALIBRATION_REMAINING_RECEIVED_AT

    value = data.get("mq4_calibration_remaining_seconds")

    if value is None:
        return

    try:
        LAST_CALIBRATION_REMAINING_SECONDS = int(float(value))
        LAST_CALIBRATION_REMAINING_RECEIVED_AT = datetime.now()
    except:
        pass


def get_calibration_remaining_seconds(rows):
    if get_latest_system_phase(rows) != "calibrating":
        return 0

    if LAST_CALIBRATION_REMAINING_SECONDS is not None and LAST_CALIBRATION_REMAINING_RECEIVED_AT is not None:
        elapsed = int((datetime.now() - LAST_CALIBRATION_REMAINING_RECEIVED_AT).total_seconds())
        remaining = LAST_CALIBRATION_REMAINING_SECONDS - elapsed

        if remaining < 0:
            remaining = 0

        return remaining

    return "-"


def build_minute_pages(rows):
    pages_by_minute = {}

    for row in rows:
        timestamp = get_cell(row, IDX_TIMESTAMP)
        dt = parse_timestamp(timestamp)

        if dt is None:
            continue

        minute_start = dt.replace(second=0, microsecond=0)

        if len(timestamp) > 8:
            minute_key = minute_start.strftime("%Y-%m-%d %H:%M")
        else:
            minute_key = minute_start.strftime("%H:%M")

        slot = dt.second // SAMPLE_INTERVAL_SECONDS

        if slot < 0 or slot >= SAMPLES_PER_PAGE:
            continue

        if minute_key not in pages_by_minute:
            labels = []

            for i in range(SAMPLES_PER_PAGE):
                label_time = minute_start + timedelta(seconds=i * SAMPLE_INTERVAL_SECONDS)
                labels.append(label_time.strftime("%H:%M:%S"))

            pages_by_minute[minute_key] = {
                "rows": [],
                "labels": labels,
                "values_mq4_raw": [None] * SAMPLES_PER_PAGE,
                "values_mq4_baseline": [None] * SAMPLES_PER_PAGE,
                "values_co2": [None] * SAMPLES_PER_PAGE,
                "minute_max_mq4_value": None,
                "minute_max_mq4_timestamp": "-",
                "minute_max_co2_value": None,
                "minute_max_co2_timestamp": "-"
            }

        page = pages_by_minute[minute_key]
        page["rows"].append(row)

        mq4_raw_value = parse_float_cell(get_cell(row, IDX_MQ4_RAW))
        mq4_baseline_value = parse_float_cell(get_cell(row, IDX_MQ4_BASELINE_RAW))
        mq4_percent_value = parse_float_cell(get_cell(row, IDX_MQ4_RELATIVE_PERCENT))
        co2_value = parse_float_cell(get_cell(row, IDX_CO2))

        if mq4_raw_value is not None:
            page["values_mq4_raw"][slot] = mq4_raw_value

        if mq4_baseline_value is not None:
            page["values_mq4_baseline"][slot] = mq4_baseline_value

        if mq4_percent_value is not None:
            if mq4_percent_value < 0:
                mq4_percent_value = 0

            if page["minute_max_mq4_value"] is None or mq4_percent_value > page["minute_max_mq4_value"]:
                page["minute_max_mq4_value"] = mq4_percent_value
                page["minute_max_mq4_timestamp"] = timestamp

        if co2_value is not None:
            page["values_co2"][slot] = co2_value

            if page["minute_max_co2_value"] is None or co2_value > page["minute_max_co2_value"]:
                page["minute_max_co2_value"] = co2_value
                page["minute_max_co2_timestamp"] = timestamp

    pages = []

    for minute_key in sorted(pages_by_minute.keys()):
        page = pages_by_minute[minute_key]

        if page["minute_max_mq4_value"] is None:
            page["minute_max_mq4_value"] = "-"
        else:
            page["minute_max_mq4_value"] = round(page["minute_max_mq4_value"], 2)

        if page["minute_max_co2_value"] is None:
            page["minute_max_co2_value"] = "-"
        else:
            page["minute_max_co2_value"] = round(page["minute_max_co2_value"], 2)

        pages.append(page)

    return pages


def read_all_csv_rows():
    rows = []

    if os.path.exists(FILENAME):
        with open(FILENAME, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=";")

            for row in reader:
                if not row or not any(cell.strip() for cell in row):
                    continue

                if row[0].lower() == "timestamp":
                    continue

                rows.append(row)

    return rows


def get_current_test_rows():
    rows = read_all_csv_rows()

    if not rows:
        return []

    current_test_id = None

    for row in reversed(rows):
        row_test_id = get_row_test_id(row)

        if row_test_id:
            current_test_id = row_test_id
            break

    if current_test_id is None:
        return rows

    current_rows = []

    for row in rows:
        if get_row_test_id(row) == current_test_id:
            current_rows.append(row)

    return current_rows


def get_latest_temperature(rows):
    for row in reversed(rows):
        temperature = get_cell(row, IDX_TEMPERATURA).strip()

        if temperature:
            return temperature

    return "-"


def get_test_max(rows, value_index, clamp_negative=False):
    test_max_value = None
    test_max_timestamp = "-"

    for row in rows:
        value = parse_float_cell(get_cell(row, value_index))

        if value is not None:
            if clamp_negative and value < 0:
                value = 0

            if test_max_value is None or value > test_max_value:
                test_max_value = value
                test_max_timestamp = get_cell(row, IDX_TIMESTAMP)

    if test_max_value is None:
        return "-", "-"

    return round(test_max_value, 2), test_max_timestamp


def get_map_points():
    current_rows = get_current_test_rows()
    measuring_rows = get_measuring_rows(current_rows)

    points = []

    for row in measuring_rows:
        latitude = parse_float_cell(get_cell(row, IDX_LATITUDE))
        longitude = parse_float_cell(get_cell(row, IDX_LONGITUDE))
        methane_percent = parse_float_cell(get_cell(row, IDX_MQ4_RELATIVE_PERCENT))

        if latitude is None or longitude is None or methane_percent is None:
            continue

        if methane_percent < 0:
            methane_percent = 0

        points.append({
            "timestamp": get_cell(row, IDX_TIMESTAMP),
            "latitude": latitude,
            "longitude": longitude,
            "methane_percent": round(methane_percent, 2),
            "mq4_raw": get_cell(row, IDX_MQ4_RAW),
            "baseline": get_cell(row, IDX_MQ4_BASELINE_RAW)
        })

    return points


@app.route("/")
def index():
    return redirect("/live")


@app.route("/dados", methods=["POST"])
def receber_dados():
    data = request.get_json()

    if not data:
        return jsonify({"erro": "JSON invalido"}), 400

    update_latest_calibration_remaining(data)

    mq4_raw = data.get("mq4_raw")

    if mq4_raw is None:
        mq4_raw = data.get("ndir_ppm")

    mq4_relative_percent = data.get("mq4_relative_percent")

    if mq4_relative_percent is None:
        mq4_relative_percent = data.get("mq4_percent")

    system_phase = data.get("system_phase", "")

    if not system_phase:
        if data.get("mq4_calibrated") == 1:
            system_phase = "measuring"
        else:
            system_phase = "calibrating"

    with open(FILENAME, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")

        writer.writerow([
            data.get("timestamp"),
            format_float(data.get("latitude"), 6),
            format_float(data.get("longitude"), 6),
            format_float(mq4_raw, 2),
            format_float(mq4_relative_percent, 2),
            format_float(data.get("co2_ppm"), 2),
            format_float(data.get("temperatura_c"), 2),
            format_float(data.get("humidade_percent"), 2),
            format_int(data.get("gps_fix")),
            format_int(data.get("gps_satelites")),
            format_float(data.get("gps_hdop"), 2),
            format_int(data.get("gps_age_ms")),
            format_float(data.get("mq4_voltage_adc"), 3),
            format_float(data.get("mq4_voltage_sensor"), 3),
            format_int(data.get("mq4_baseline_raw")),
            format_float(data.get("mq4_delta"), 2),
            format_int(data.get("mq4_calibrated")),
            system_phase,
            data.get("test_id", "sem_test_id")
        ])

    print("Dados recebidos:", data)
    return jsonify({"status": "ok"}), 200


@app.route("/api/force_baseline", methods=["POST"])
def api_force_baseline():
    global PENDING_COMMAND

    PENDING_COMMAND["command"] = "force_baseline"
    PENDING_COMMAND["sample_count"] = FORCE_BASELINE_SAMPLE_COUNT
    PENDING_COMMAND["command_id"] = PENDING_COMMAND["command_id"] + 1

    print("Comando criado: force_baseline")

    return jsonify({
        "status": "ok",
        "command": "force_baseline",
        "sample_count": FORCE_BASELINE_SAMPLE_COUNT,
        "command_id": PENDING_COMMAND["command_id"],
        "message": "Comando enviado. O ESP32 vai usar a média dos últimos 6 valores como baseline."
    })


@app.route("/api/command")
def api_command():
    global PENDING_COMMAND

    if PENDING_COMMAND["command"] == "force_baseline":
        response = {
            "command": "force_baseline",
            "sample_count": PENDING_COMMAND["sample_count"],
            "command_id": PENDING_COMMAND["command_id"]
        }

        PENDING_COMMAND["command"] = "none"

        return jsonify(response)

    return jsonify({
        "command": "none",
        "command_id": PENDING_COMMAND["command_id"]
    })


@app.route("/download")
def download_csv():
    if os.path.exists(FILENAME):
        return send_file(FILENAME, as_attachment=False)

    return "Ficheiro não encontrado", 404


@app.route("/api/live_data")
def api_live_data():
    current_rows = get_current_test_rows()
    measuring_rows = get_measuring_rows(current_rows)

    if not current_rows:
        return jsonify({
            "pages": [],
            "total_pages": 0,
            "test_start_timestamp": "-",
            "current_temperature": "-",
            "current_system_phase": "-",
            "current_mq4_baseline": "-",
            "calibration_remaining_seconds": "-",
            "pending_command": PENDING_COMMAND["command"],
            "test_max_mq4_value": "-",
            "test_max_mq4_timestamp": "-",
            "test_max_co2_value": "-",
            "test_max_co2_timestamp": "-",
            "all_rows": []
        })

    test_start_timestamp = "-"
    if measuring_rows:
        test_start_timestamp = get_cell(measuring_rows[0], IDX_TIMESTAMP)

    test_max_mq4_value, test_max_mq4_timestamp = get_test_max(
        measuring_rows,
        IDX_MQ4_RELATIVE_PERCENT,
        clamp_negative=True
    )

    test_max_co2_value, test_max_co2_timestamp = get_test_max(
        measuring_rows,
        IDX_CO2
    )

    pages = build_minute_pages(measuring_rows)

    return jsonify({
        "pages": pages,
        "total_pages": len(pages),
        "test_start_timestamp": test_start_timestamp,
        "current_temperature": get_latest_temperature(current_rows),
        "current_system_phase": get_latest_system_phase(current_rows),
        "current_mq4_baseline": get_latest_baseline(current_rows),
        "calibration_remaining_seconds": get_calibration_remaining_seconds(current_rows),
        "pending_command": PENDING_COMMAND["command"],
        "test_max_mq4_value": test_max_mq4_value,
        "test_max_mq4_timestamp": test_max_mq4_timestamp,
        "test_max_co2_value": test_max_co2_value,
        "test_max_co2_timestamp": test_max_co2_timestamp,
        "all_rows": measuring_rows
    })


@app.route("/api/map_data")
def api_map_data():
    current_rows = get_current_test_rows()

    return jsonify({
        "points": get_map_points(),
        "current_system_phase": get_latest_system_phase(current_rows),
        "current_mq4_baseline": get_latest_baseline(current_rows),
        "current_temperature": get_latest_temperature(current_rows)
    })


@app.route("/mapa")
def mapa():
    return """
    <html>
    <head>
        <title>Mapa de metano</title>

        <link
            rel="stylesheet"
            href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        />

        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script src="https://unpkg.com/leaflet.heat/dist/leaflet-heat.js"></script>

        <style>
            body {
                margin: 0;
                font-family: Arial, sans-serif;
                font-size: 13px;
            }

            .top-bar {
                padding: 10px;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 10px;
                background-color: #f7f7f7;
                border-bottom: 1px solid #ccc;
            }

            .title-area {
                display: flex;
                align-items: center;
                gap: 12px;
                flex-wrap: wrap;
            }

            h1 {
                font-size: 18px;
                margin: 0;
            }

            .badge {
                font-size: 13px;
                font-weight: bold;
                padding: 6px 10px;
                border: 1px solid #ccc;
                border-radius: 6px;
                background-color: white;
            }

            .badge.calibrating {
                background-color: #fff3e8;
                border-color: #ffbd7a;
            }

            .badge.measuring {
                background-color: #e8f7e8;
                border-color: #85c985;
            }

            button {
                padding: 7px 12px;
                font-size: 13px;
                cursor: pointer;
                border: 1px solid #999;
                border-radius: 5px;
                background-color: white;
            }

            .mode-button {
                font-weight: bold;
                background-color: #e8f4ff;
                border-color: #99c9ff;
            }

            #map {
                width: 100%;
                height: calc(100vh - 58px);
            }

            .legend {
                background: white;
                padding: 10px;
                border-radius: 6px;
                border: 1px solid #ccc;
                line-height: 1.5;
                font-size: 12px;
            }

            .legend-row {
                display: flex;
                align-items: center;
                gap: 6px;
            }

            .legend-color {
                width: 14px;
                height: 14px;
                border-radius: 50%;
                display: inline-block;
            }
        </style>
    </head>

    <body>
        <div class="top-bar">
            <div class="title-area">
                <h1>Mapa de metano relativo</h1>
                <span class="badge" id="statusBadge">Estado: -</span>
                <span class="badge" id="baselineBadge">Baseline: -</span>
                <span class="badge" id="pointsBadge">Pontos: 0</span>
                <span class="badge" id="temperatureBadge">Temperatura: -</span>
                <button class="mode-button" id="mapModeButton" onclick="toggleMapMode()">Ver heatmap</button>
            </div>
        </div>

        <div id="map"></div>

        <script>
            const map = L.map('map').setView([38.7555, -9.1155], 17);

            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 22,
                attribution: '&copy; OpenStreetMap contributors'
            }).addTo(map);

            const pointsLayer = L.layerGroup();
            let heatLayer = null;

            let hasFittedBounds = false;
            let mapMode = "points";
            let latestPoints = [];

            function getColor(value) {
                if (value < 10) {
                    return '#2ecc71';
                }

                if (value < 30) {
                    return '#f1c40f';
                }

                if (value < 60) {
                    return '#e67e22';
                }

                return '#e74c3c';
            }

            function getRadius(value) {
                if (value < 10) {
                    return 5;
                }

                if (value < 30) {
                    return 6;
                }

                if (value < 60) {
                    return 7;
                }

                return 8;
            }

            function formatPercent(value) {
                if (value === null || value === undefined || value === "") {
                    return "-";
                }

                let numberValue = Number(value);

                if (isNaN(numberValue)) {
                    return "-";
                }

                if (numberValue < 0) {
                    numberValue = 0;
                }

                return "+" + numberValue.toFixed(2) + "%";
            }

            function renderStatus(systemPhase) {
                const badge = document.getElementById("statusBadge");

                badge.classList.remove("calibrating");
                badge.classList.remove("measuring");

                if (systemPhase === "calibrating") {
                    badge.textContent = "Estado: A calibrar sensor de metano";
                    badge.classList.add("calibrating");
                } else if (systemPhase === "measuring") {
                    badge.textContent = "Estado: Medição ativa";
                    badge.classList.add("measuring");
                } else {
                    badge.textContent = "Estado: -";
                }
            }

            function renderBadges(data) {
                renderStatus(data.current_system_phase);

                document.getElementById("baselineBadge").textContent =
                    "Baseline: " + (data.current_mq4_baseline || "-");

                document.getElementById("pointsBadge").textContent =
                    "Pontos: " + ((data.points || []).length);

                if (data.current_temperature && data.current_temperature !== "-") {
                    document.getElementById("temperatureBadge").textContent =
                        "Temperatura: " + data.current_temperature + " °C";
                } else {
                    document.getElementById("temperatureBadge").textContent =
                        "Temperatura: -";
                }
            }

            function rebuildPointsLayer(points) {
                pointsLayer.clearLayers();

                const boundsPoints = [];

                for (const point of points) {
                    const color = getColor(point.methane_percent);
                    const radius = getRadius(point.methane_percent);

                    const marker = L.circleMarker(
                        [point.latitude, point.longitude],
                        {
                            radius: radius,
                            color: color,
                            fillColor: color,
                            fillOpacity: 0.75,
                            weight: 2
                        }
                    );

                    marker.bindPopup(
                        "<b>Metano relativo:</b> " + formatPercent(point.methane_percent) + "<br>" +
                        "<b>Timestamp:</b> " + point.timestamp + "<br>" +
                        "<b>Latitude:</b> " + point.latitude.toFixed(6) + "<br>" +
                        "<b>Longitude:</b> " + point.longitude.toFixed(6) + "<br>" +
                        "<b>Raw:</b> " + point.mq4_raw + "<br>" +
                        "<b>Baseline:</b> " + point.baseline
                    );

                    marker.addTo(pointsLayer);
                    boundsPoints.push([point.latitude, point.longitude]);
                }

                if (!hasFittedBounds && boundsPoints.length > 0) {
                    const bounds = L.latLngBounds(boundsPoints);
                    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 19 });
                    hasFittedBounds = true;
                }
            }

            function rebuildHeatLayer(points) {
                const heatData = [];

                for (const point of points) {
                    let intensity = Number(point.methane_percent);

                    if (isNaN(intensity) || intensity < 0) {
                        intensity = 0;
                    }

                    if (intensity > 100) {
                        intensity = 100;
                    }

                    let normalizedIntensity = intensity / 100.0;

                    if (normalizedIntensity < 0.05) {
                        normalizedIntensity = 0.05;
                    }

                    heatData.push([
                        point.latitude,
                        point.longitude,
                        normalizedIntensity
                    ]);
                }

                heatLayer = L.heatLayer(heatData, {
                    radius: 32,
                    blur: 24,
                    maxZoom: 19,
                    max: 1.0,
                    gradient: {
                        0.00: '#2ecc71',
                        0.20: '#f1c40f',
                        0.50: '#e67e22',
                        0.80: '#e74c3c'
                    }
                });
            }

            function removeAllDataLayers() {
                if (map.hasLayer(pointsLayer)) {
                    map.removeLayer(pointsLayer);
                }

                if (heatLayer && map.hasLayer(heatLayer)) {
                    map.removeLayer(heatLayer);
                }
            }

            function applyMapMode() {
                const button = document.getElementById("mapModeButton");

                removeAllDataLayers();

                if (mapMode === "points") {
                    pointsLayer.addTo(map);
                    button.textContent = "Ver heatmap";
                } else {
                    if (heatLayer) {
                        heatLayer.addTo(map);
                    }

                    button.textContent = "Ver pontos";
                }

                setTimeout(function() {
                    map.invalidateSize();
                }, 100);
            }

            function toggleMapMode() {
                if (mapMode === "points") {
                    mapMode = "heatmap";
                } else {
                    mapMode = "points";
                }

                applyMapMode();
            }

            async function fetchMapData() {
                try {
                    const response = await fetch('/api/map_data');
                    const data = await response.json();

                    latestPoints = data.points || [];

                    renderBadges(data);
                    rebuildPointsLayer(latestPoints);

                    if (heatLayer && map.hasLayer(heatLayer)) {
                        map.removeLayer(heatLayer);
                    }

                    rebuildHeatLayer(latestPoints);
                    applyMapMode();
                } catch (err) {
                    console.error("Erro ao atualizar mapa:", err);
                }
            }

            const legend = L.control({ position: 'bottomright' });

            legend.onAdd = function() {
                const div = L.DomUtil.create('div', 'legend');

                div.innerHTML = `
                    <b>Metano relativo</b><br>
                    <div class="legend-row">
                        <span class="legend-color" style="background:#2ecc71"></span>
                        +0% a +10%
                    </div>
                    <div class="legend-row">
                        <span class="legend-color" style="background:#f1c40f"></span>
                        +10% a +30%
                    </div>
                    <div class="legend-row">
                        <span class="legend-color" style="background:#e67e22"></span>
                        +30% a +60%
                    </div>
                    <div class="legend-row">
                        <span class="legend-color" style="background:#e74c3c"></span>
                        +60% a +100%
                    </div>
                `;

                return div;
            };

            legend.addTo(map);

            fetchMapData();
            setInterval(fetchMapData, 2000);
        </script>
    </body>
    </html>
    """


@app.route("/live")
def live():
    return """
    <html>
    <head>
        <title>Monitorização CH4 e CO2</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 10px;
                font-size: 12px;
            }

            .top-bar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 10px;
                gap: 12px;
            }

            .title-area {
                display: flex;
                align-items: center;
                gap: 18px;
                flex-wrap: wrap;
            }

            h1 {
                font-size: 18px;
                margin: 0;
            }

            .temperature-badge,
            .status-badge {
                font-size: 14px;
                font-weight: bold;
                padding: 6px 10px;
                border: 1px solid #ccc;
                border-radius: 6px;
                background-color: #f7f7f7;
            }

            .status-badge.calibrating {
                background-color: #fff3e8;
                border-color: #ffbd7a;
            }

            .status-badge.measuring {
                background-color: #e8f7e8;
                border-color: #85c985;
            }

            .button-container {
                margin: 0;
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
            }

            .chart-nav {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
                margin-bottom: 10px;
                flex-wrap: wrap;
            }

            .chart-nav button {
                padding: 6px 10px;
                font-size: 14px;
                cursor: pointer;
            }

            .main-content {
                display: flex;
                gap: 16px;
                align-items: stretch;
                margin-bottom: 20px;
            }

            .chart-container {
                flex: 3;
                min-width: 0;
                height: 300px;
            }

            .summary-box {
                flex: 1;
                min-width: 260px;
            }

            .summary-box table {
                width: 100%;
                height: 100%;
                border-collapse: collapse;
                font-size: 11px;
            }

            .summary-box th,
            .summary-box td {
                border: 1px solid #ccc;
                padding: 6px 8px;
                text-align: center;
            }

            .summary-box th {
                background-color: #f2f2f2;
            }

            .table-container {
                max-height: 50vh;
                overflow-y: auto;
                border: 1px solid #ccc;
            }

            table {
                border-collapse: collapse;
                width: 100%;
                font-size: 11px;
            }

            th, td {
                border: 1px solid #ccc;
                padding: 4px 6px;
                text-align: center;
            }

            th {
                background-color: #f2f2f2;
                position: sticky;
                top: 0;
            }

            button {
                padding: 8px 12px;
                font-size: 12px;
                cursor: pointer;
            }

            #pageInfo {
                font-size: 12px;
                font-weight: bold;
                min-width: 90px;
                text-align: center;
            }

            .live-button {
                background-color: #e8f4ff;
                border: 1px solid #99c9ff;
            }

            .toggle-button {
                background-color: #fff3e8;
                border: 1px solid #ffbd7a;
                font-weight: bold;
            }

            .force-button {
                background-color: #e8f7e8;
                border: 1px solid #85c985;
                font-weight: bold;
            }
        </style>
    </head>

    <body>
        <div class="top-bar">
            <div class="title-area">
                <h1 id="testTitle">Teste iniciado a: -</h1>
                <span class="temperature-badge" id="temperatureBadge">Temperatura: -</span>
                <span class="status-badge" id="statusBadge">Estado: -</span>
                <span class="status-badge calibrating" id="calibrationCountdownBadge" style="display:none;">
                    Calibração: -
                </span>
            </div>

            <div class="button-container">
                <button id="forceBaselineButton" class="force-button" onclick="forceBaselineNow()">
                    Começar teste agora
                </button>

                <a href="/download" target="_blank">
                    <button>Abrir CSV completo</button>
                </a>

                <a href="/mapa" target="_blank">
                    <button>Abrir mapa</button>
                </a>
            </div>
        </div>

        <div class="chart-nav">
            <button onclick="previousPage()">←</button>
            <span id="pageInfo"></span>
            <button onclick="nextPage()">→</button>
            <button class="live-button" onclick="jumpToLive()">Jump to live</button>
            <button class="toggle-button" id="toggleSensorButton" onclick="toggleSensor()">Ver CO2</button>
        </div>

        <div class="main-content">
            <div class="chart-container">
                <canvas id="sensorChart"></canvas>
            </div>

            <div class="summary-box">
                <table>
                    <tr>
                        <th id="minuteMaxLabel">Máximo aumento metano deste minuto</th>
                        <td id="minuteMaxValue">-</td>
                    </tr>
                    <tr>
                        <th id="minuteMaxTimestampLabel">Timestamp do máximo aumento metano deste minuto</th>
                        <td id="minuteMaxTimestamp">-</td>
                    </tr>
                    <tr>
                        <th id="testMaxLabel">Máximo aumento metano do teste</th>
                        <td id="testMaxValue">-</td>
                    </tr>
                    <tr>
                        <th id="testMaxTimestampLabel">Timestamp do máximo aumento metano do teste</th>
                        <td id="testMaxTimestamp">-</td>
                    </tr>
                </table>
            </div>
        </div>

        <div class="table-container" id="tableContainer">
            <table id="dataTable">
                <thead>
                    <tr>
                        <th>timestamp</th>
                        <th>latitude</th>
                        <th>longitude</th>
                        <th>valor</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>

        <script>
            let pages = [];
            let allRows = [];
            let currentPage = 0;
            let liveMode = true;
            let activeSensor = "mq4";
            let latestData = null;

            const MQ4_BLUE = 'rgb(54, 162, 235)';
            const CO2_RED = 'red';
            const BASELINE_GREY = 'rgb(120, 120, 120)';

            const IDX_TIMESTAMP = 0;
            const IDX_LATITUDE = 1;
            const IDX_LONGITUDE = 2;
            const IDX_MQ4_RELATIVE_PERCENT = 4;
            const IDX_CO2 = 5;

            const ctx = document.getElementById('sensorChart').getContext('2d');

            const chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: 'Metano',
                            data: [],
                            fill: false,
                            tension: 0.1,
                            borderWidth: 2,
                            borderColor: MQ4_BLUE,
                            backgroundColor: MQ4_BLUE,
                            pointRadius: 4,
                            pointHoverRadius: 5,
                            spanGaps: false
                        },
                        {
                            label: 'Baseline metano',
                            data: [],
                            fill: false,
                            tension: 0,
                            borderWidth: 2,
                            borderColor: BASELINE_GREY,
                            backgroundColor: BASELINE_GREY,
                            borderDash: [6, 4],
                            pointRadius: 0,
                            pointHoverRadius: 0,
                            spanGaps: true,
                            showInLegend: false
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {
                        legend: {
                            labels: {
                                filter: function(legendItem, chartData) {
                                    const dataset = chartData.datasets[legendItem.datasetIndex];
                                    return dataset.showInLegend !== false;
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            title: {
                                display: true,
                                text: 'Tempo'
                            }
                        },
                        y: {
                            title: {
                                display: true,
                                text: 'Metano'
                            }
                        }
                    }
                }
            });

            function formatMethanePercent(value) {
                if (value === null || value === undefined || value === "") {
                    return "";
                }

                let numberValue = Number(String(value).replace(",", "."));

                if (isNaN(numberValue)) {
                    return "";
                }

                if (numberValue < 0) {
                    numberValue = 0;
                }

                return "+" + numberValue.toFixed(2) + "%";
            }

            function formatSummaryValue(value) {
                const config = getActiveConfig();

                if (value === null || value === undefined || value === "-") {
                    return "-";
                }

                if (config.valueMode === "methane_percent") {
                    return formatMethanePercent(value);
                }

                return value;
            }

            function getActiveConfig() {
                if (activeSensor === "co2") {
                    return {
                        sensorName: "CO2",
                        graphLabel: "CO2 (ppm)",
                        yAxisLabel: "CO2 (ppm)",
                        color: CO2_RED,
                        valuesKey: "values_co2",
                        baselineValuesKey: null,
                        minuteMaxValueKey: "minute_max_co2_value",
                        minuteMaxTimestampKey: "minute_max_co2_timestamp",
                        testMaxValueKey: "test_max_co2_value",
                        testMaxTimestampKey: "test_max_co2_timestamp",
                        rowValueIndex: IDX_CO2,
                        toggleText: "Ver metano",
                        valueMode: "co2",

                        minuteMaxLabel: "Máximo CO2 deste minuto",
                        minuteMaxTimestampLabel: "Timestamp do máximo CO2 deste minuto",
                        testMaxLabel: "Máximo CO2 do teste",
                        testMaxTimestampLabel: "Timestamp do máximo CO2 do teste"
                    };
                }

                return {
                    sensorName: "Metano",
                    graphLabel: "Metano",
                    yAxisLabel: "Metano",
                    color: MQ4_BLUE,
                    valuesKey: "values_mq4_raw",
                    baselineValuesKey: "values_mq4_baseline",
                    minuteMaxValueKey: "minute_max_mq4_value",
                    minuteMaxTimestampKey: "minute_max_mq4_timestamp",
                    testMaxValueKey: "test_max_mq4_value",
                    testMaxTimestampKey: "test_max_mq4_timestamp",
                    rowValueIndex: IDX_MQ4_RELATIVE_PERCENT,
                    toggleText: "Ver CO2",
                    valueMode: "methane_percent",

                    minuteMaxLabel: "Máximo aumento metano deste minuto",
                    minuteMaxTimestampLabel: "Timestamp do máximo aumento metano deste minuto",
                    testMaxLabel: "Máximo aumento metano do teste",
                    testMaxTimestampLabel: "Timestamp do máximo aumento metano do teste"
                };
            }

            function updateToggleButton() {
                const config = getActiveConfig();
                document.getElementById("toggleSensorButton").textContent = config.toggleText;
            }

            function renderChart() {
                const config = getActiveConfig();

                chart.data.datasets[0].label = config.graphLabel;
                chart.data.datasets[0].borderColor = config.color;
                chart.data.datasets[0].backgroundColor = config.color;
                chart.options.scales.y.title.text = config.yAxisLabel;

                if (!pages.length) {
                    document.getElementById("pageInfo").textContent = "Sem medição";
                    chart.data.labels = [];
                    chart.data.datasets[0].data = [];
                    chart.data.datasets[1].data = [];
                    chart.update('none');
                    return;
                }

                if (liveMode) {
                    currentPage = pages.length - 1;
                }

                const page = pages[currentPage];

                chart.data.labels = page.labels;
                chart.data.datasets[0].data = page[config.valuesKey];

                if (config.baselineValuesKey) {
                    chart.data.datasets[1].data = page[config.baselineValuesKey];
                    chart.data.datasets[1].hidden = false;
                } else {
                    chart.data.datasets[1].data = [];
                    chart.data.datasets[1].hidden = true;
                }

                chart.update('none');

                document.getElementById("pageInfo").textContent =
                    "Minuto " + (currentPage + 1) + " / " + pages.length;
            }

            function renderTable() {
                const config = getActiveConfig();
                const tbody = document.querySelector("#dataTable tbody");
                const container = document.getElementById("tableContainer");

                tbody.innerHTML = "";

                for (const row of allRows) {
                    const tr = document.createElement("tr");

                    const timestamp = row[IDX_TIMESTAMP] || "";
                    const latitude = row[IDX_LATITUDE] || "";
                    const longitude = row[IDX_LONGITUDE] || "";

                    let value = row[config.rowValueIndex] || "";

                    if (config.valueMode === "methane_percent") {
                        value = formatMethanePercent(value);
                    }

                    const cells = [timestamp, latitude, longitude, value];

                    for (const cell of cells) {
                        const td = document.createElement("td");
                        td.textContent = cell;
                        tr.appendChild(td);
                    }

                    tbody.appendChild(tr);
                }

                container.scrollTop = container.scrollHeight;
            }

            function renderSummary() {
                const config = getActiveConfig();

                document.getElementById("minuteMaxLabel").textContent = config.minuteMaxLabel;
                document.getElementById("minuteMaxTimestampLabel").textContent = config.minuteMaxTimestampLabel;
                document.getElementById("testMaxLabel").textContent = config.testMaxLabel;
                document.getElementById("testMaxTimestampLabel").textContent = config.testMaxTimestampLabel;

                if (!pages.length || !latestData) {
                    document.getElementById("minuteMaxValue").textContent = "-";
                    document.getElementById("minuteMaxTimestamp").textContent = "-";
                    document.getElementById("testMaxValue").textContent = "-";
                    document.getElementById("testMaxTimestamp").textContent = "-";
                    return;
                }

                const page = pages[currentPage];

                document.getElementById("minuteMaxValue").textContent =
                    formatSummaryValue(page[config.minuteMaxValueKey]);

                document.getElementById("minuteMaxTimestamp").textContent =
                    page[config.minuteMaxTimestampKey] ?? "-";

                document.getElementById("testMaxValue").textContent =
                    formatSummaryValue(latestData[config.testMaxValueKey]);

                document.getElementById("testMaxTimestamp").textContent =
                    latestData[config.testMaxTimestampKey] ?? "-";
            }

            function renderTemperature(currentTemperature) {
                const badge = document.getElementById("temperatureBadge");

                if (!currentTemperature || currentTemperature === "-") {
                    badge.textContent = "Temperatura: -";
                } else {
                    badge.textContent = "Temperatura: " + currentTemperature + " °C";
                }
            }

            function formatCountdown(seconds) {
                let value = Number(seconds);

                if (isNaN(value) || value < 0) {
                    return "-";
                }

                value = Math.floor(value);

                const minutes = Math.floor(value / 60);
                const secs = value % 60;

                return String(minutes).padStart(2, "0") + ":" + String(secs).padStart(2, "0");
            }

            function renderCalibrationCountdown(systemPhase, remainingSeconds) {
                const badge = document.getElementById("calibrationCountdownBadge");

                if (!badge) {
                    return;
                }

                if (systemPhase === "calibrating") {
                    badge.style.display = "inline-block";
                    badge.textContent = "Calibração: " + formatCountdown(remainingSeconds);
                } else {
                    badge.style.display = "none";
                }
            }

            function renderStatus(systemPhase, baseline) {
                const badge = document.getElementById("statusBadge");

                badge.classList.remove("calibrating");
                badge.classList.remove("measuring");

                if (systemPhase === "calibrating") {
                    badge.textContent = "Estado: A calibrar sensor de metano";
                    badge.classList.add("calibrating");
                } else if (systemPhase === "measuring") {
                    if (baseline && baseline !== "-") {
                        badge.textContent = "Estado: Medição ativa | Baseline metano: " + baseline;
                    } else {
                        badge.textContent = "Estado: Medição ativa";
                    }

                    badge.classList.add("measuring");
                } else {
                    badge.textContent = "Estado: -";
                }
            }

            function renderForceBaselineButton(systemPhase) {
                const button = document.getElementById("forceBaselineButton");

                if (!button) {
                    return;
                }

                if (systemPhase === "measuring") {
                    button.style.display = "none";
                } else {
                    button.style.display = "inline-block";
                }
            }

            function toggleSensor() {
                if (activeSensor === "mq4") {
                    activeSensor = "co2";
                } else {
                    activeSensor = "mq4";
                }

                updateToggleButton();
                renderChart();
                renderTable();
                renderSummary();
            }

            async function forceBaselineNow() {
                const confirmed = confirm(
                    "Usar a média dos últimos 6 valores do ESP32 como baseline e começar o teste agora?"
                );

                if (!confirmed) {
                    return;
                }

                try {
                    const response = await fetch('/api/force_baseline', {
                        method: 'POST'
                    });

                    const data = await response.json();

                    const button = document.getElementById("forceBaselineButton");
                    if (button) {
                        button.style.display = "none";
                    }

                    alert(data.message || "Comando enviado para o ESP32.");
                } catch (err) {
                    alert("Erro ao enviar comando para o ESP32.");
                    console.error(err);
                }
            }

            async function fetchData() {
                try {
                    const response = await fetch('/api/live_data');
                    const data = await response.json();

                    latestData = data;
                    pages = data.pages || [];
                    allRows = data.all_rows || [];

                    document.getElementById("testTitle").textContent =
                        "Teste iniciado a: " + (data.test_start_timestamp || "-");

                    renderTemperature(data.current_temperature);
                    renderStatus(data.current_system_phase, data.current_mq4_baseline);
                    renderCalibrationCountdown(
                        data.current_system_phase,
                        data.calibration_remaining_seconds
                    );
                    renderForceBaselineButton(data.current_system_phase);

                    if (!liveMode && currentPage >= pages.length) {
                        currentPage = Math.max(0, pages.length - 1);
                    }

                    updateToggleButton();
                    renderChart();
                    renderTable();
                    renderSummary();
                } catch (err) {
                    console.error("Erro ao atualizar dados:", err);
                }
            }

            function previousPage() {
                if (currentPage > 0) {
                    currentPage--;
                    liveMode = false;
                    renderChart();
                    renderSummary();
                }
            }

            function nextPage() {
                if (currentPage < pages.length - 1) {
                    currentPage++;
                    liveMode = false;
                } else {
                    liveMode = true;
                }

                renderChart();
                renderSummary();
            }

            function jumpToLive() {
                liveMode = true;
                currentPage = pages.length - 1;

                renderChart();
                renderSummary();
            }

            window.onload = function() {
                fetchData();
                setInterval(fetchData, 1000);
            };
        </script>
    </body>
    </html>
    """


init_csv()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)