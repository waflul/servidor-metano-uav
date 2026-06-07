from flask import Flask, request, jsonify, send_file, redirect
import csv
import os
from datetime import datetime, timedelta

app = Flask(__name__)

FILENAME = "dados_recebidos.csv"

# 12 amostras x 5 segundos = 1 minuto
SAMPLES_PER_PAGE = 12
SAMPLE_INTERVAL_SECONDS = 5


def init_csv():
    file_exists = os.path.exists(FILENAME)

    with open(FILENAME, "a", newline="", encoding="utf-8") as f:
        if file_exists:
            f.write("\n\n")

        writer = csv.writer(f, delimiter=";")

        if not file_exists:
            writer.writerow(["timestamp", "latitude", "longitude", "ndir_ppm", "test_id"])


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


def build_minute_pages(rows):
    pages_by_minute = {}

    for row in rows:
        if len(row) < 4:
            continue

        timestamp = row[0]
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
                "values": [None] * SAMPLES_PER_PAGE,
                "minute_max_value": None,
                "minute_max_timestamp": "-"
            }

        page = pages_by_minute[minute_key]
        page["rows"].append(row)

        try:
            value = float(row[3].replace(",", "."))
        except:
            value = None

        if value is not None:
            page["values"][slot] = value

            if page["minute_max_value"] is None or value > page["minute_max_value"]:
                page["minute_max_value"] = value
                page["minute_max_timestamp"] = timestamp

    pages = []

    for minute_key in sorted(pages_by_minute.keys()):
        page = pages_by_minute[minute_key]

        if page["minute_max_value"] is None:
            page["minute_max_value"] = 0

        page["minute_max_value"] = round(page["minute_max_value"], 2)

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
        if len(row) >= 5 and row[4].strip():
            current_test_id = row[4]
            break

    if current_test_id is None:
        return rows

    current_rows = []

    for row in rows:
        if len(row) >= 5 and row[4] == current_test_id:
            current_rows.append(row)

    return current_rows


@app.route("/")
def index():
    return redirect("/live")


@app.route("/dados", methods=["POST"])
def receber_dados():
    data = request.get_json()

    if not data:
        return jsonify({"erro": "JSON invalido"}), 400

    with open(FILENAME, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            data.get("timestamp"),
            f"{data.get('latitude'):.6f}" if data.get("latitude") is not None else "",
            f"{data.get('longitude'):.6f}" if data.get("longitude") is not None else "",
            f"{data.get('ndir_ppm'):.2f}" if data.get("ndir_ppm") is not None else "",
            data.get("test_id", "sem_test_id")
        ])

    print("Dados recebidos:", data)
    return jsonify({"status": "ok"}), 200


@app.route("/download")
def download_csv():
    if os.path.exists(FILENAME):
        return send_file(FILENAME, as_attachment=False)

    return "Ficheiro não encontrado", 404


@app.route("/api/live_data")
def api_live_data():
    rows = get_current_test_rows()

    if not rows:
        return jsonify({
            "pages": [],
            "total_pages": 0,
            "test_max_value": "-",
            "test_max_timestamp": "-",
            "test_start_timestamp": "-",
            "all_rows": []
        })

    test_start_timestamp = "-"
    if rows and len(rows[0]) >= 1:
        test_start_timestamp = rows[0][0]

    test_max_value = None
    test_max_timestamp = "-"

    for row in rows:
        if len(row) >= 4:
            try:
                value = float(row[3].replace(",", "."))

                if test_max_value is None or value > test_max_value:
                    test_max_value = value
                    test_max_timestamp = row[0]
            except:
                pass

    if test_max_value is None:
        test_max_value = 0

    pages = build_minute_pages(rows)

    return jsonify({
        "pages": pages,
        "total_pages": len(pages),
        "test_max_value": round(test_max_value, 2),
        "test_max_timestamp": test_max_timestamp,
        "test_start_timestamp": test_start_timestamp,
        "all_rows": rows
    })


@app.route("/live")
def live():
    return """
    <html>
    <head>
        <title>Monitorização CH4</title>
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
            }

            h1 {
                font-size: 18px;
                margin: 0;
            }

            .button-container {
                margin: 0;
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
        </style>
    </head>

    <body>
        <div class="top-bar">
            <h1 id="testTitle">Teste iniciado a: -</h1>

            <div class="button-container">
                <a href="/download" target="_blank">
                    <button>Abrir CSV completo</button>
                </a>
            </div>
        </div>

        <div class="chart-nav">
            <button onclick="previousPage()">←</button>
            <span id="pageInfo"></span>
            <button onclick="nextPage()">→</button>
            <button class="live-button" onclick="jumpToLive()">Jump to live</button>
        </div>

        <div class="main-content">
            <div class="chart-container">
                <canvas id="ndirChart"></canvas>
            </div>

            <div class="summary-box">
                <table>
                    <tr>
                        <th>Máximo deste minuto</th>
                        <td id="minuteMaxValue">-</td>
                    </tr>
                    <tr>
                        <th>Timestamp do máximo deste minuto</th>
                        <td id="minuteMaxTimestamp">-</td>
                    </tr>
                    <tr>
                        <th>Máximo do teste</th>
                        <td id="testMaxValue">-</td>
                    </tr>
                    <tr>
                        <th>Timestamp do máximo do teste</th>
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

            const THRESHOLD_VALUE = 65;
            const NDIR_BLUE = 'rgb(54, 162, 235)';
            const THRESHOLD_RED = 'red';

            const ctx = document.getElementById('ndirChart').getContext('2d');

            const chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: 'NDIR (ppm)',
                            data: [],
                            fill: false,
                            tension: 0.1,
                            borderWidth: 2,
                            borderColor: NDIR_BLUE,
                            backgroundColor: NDIR_BLUE,
                            pointRadius: 4,
                            pointHoverRadius: 5,
                            spanGaps: false,
                            pointBackgroundColor: function(context) {
                                const value = context.raw;
                                return value > THRESHOLD_VALUE ? THRESHOLD_RED : NDIR_BLUE;
                            },
                            pointBorderColor: function(context) {
                                const value = context.raw;
                                return value > THRESHOLD_VALUE ? THRESHOLD_RED : NDIR_BLUE;
                            }
                        },
                        {
                            label: '',
                            data: [],
                            fill: false,
                            tension: 0,
                            borderWidth: 2,
                            borderColor: THRESHOLD_RED,
                            backgroundColor: THRESHOLD_RED,
                            pointRadius: 0,
                            pointHoverRadius: 0
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
                                filter: function(item) {
                                    return item.text !== '';
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
                                text: 'Valor'
                            }
                        }
                    }
                }
            });

            function renderChart() {
                if (!pages.length) {
                    document.getElementById("pageInfo").textContent = "Minuto 0 / 0";
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
                chart.data.datasets[0].data = page.values;
                chart.data.datasets[1].data = page.labels.map(() => THRESHOLD_VALUE);

                chart.update('none');

                document.getElementById("pageInfo").textContent =
                    "Minuto " + (currentPage + 1) + " / " + pages.length;
            }

            function renderTable() {
                const tbody = document.querySelector("#dataTable tbody");
                const container = document.getElementById("tableContainer");

                tbody.innerHTML = "";

                for (const row of allRows) {
                    const tr = document.createElement("tr");

                    for (const cell of row.slice(0, 4)) {
                        const td = document.createElement("td");
                        td.textContent = cell;
                        tr.appendChild(td);
                    }

                    tbody.appendChild(tr);
                }

                container.scrollTop = container.scrollHeight;
            }

            function renderSummary(testMaxValue, testMaxTimestamp) {
                if (!pages.length) {
                    document.getElementById("minuteMaxValue").textContent = "-";
                    document.getElementById("minuteMaxTimestamp").textContent = "-";
                    document.getElementById("testMaxValue").textContent = "-";
                    document.getElementById("testMaxTimestamp").textContent = "-";
                    return;
                }

                const page = pages[currentPage];

                document.getElementById("minuteMaxValue").textContent = page.minute_max_value;
                document.getElementById("minuteMaxTimestamp").textContent = page.minute_max_timestamp;
                document.getElementById("testMaxValue").textContent = testMaxValue;
                document.getElementById("testMaxTimestamp").textContent = testMaxTimestamp;
            }

            async function fetchData() {
                try {
                    const response = await fetch('/api/live_data');
                    const data = await response.json();

                    pages = data.pages || [];
                    allRows = data.all_rows || [];

                    document.getElementById("testTitle").textContent =
                        "Teste iniciado a: " + (data.test_start_timestamp || "-");

                    if (!liveMode && currentPage >= pages.length) {
                        currentPage = Math.max(0, pages.length - 1);
                    }

                    renderChart();
                    renderTable();
                    renderSummary(data.test_max_value, data.test_max_timestamp);
                } catch (err) {
                    console.error("Erro ao atualizar dados:", err);
                }
            }

            function previousPage() {
                if (currentPage > 0) {
                    currentPage--;
                    liveMode = false;
                    renderChart();
                    renderSummary(
                        document.getElementById("testMaxValue").textContent,
                        document.getElementById("testMaxTimestamp").textContent
                    );
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
                renderSummary(
                    document.getElementById("testMaxValue").textContent,
                    document.getElementById("testMaxTimestamp").textContent
                );
            }

            function jumpToLive() {
                liveMode = true;
                currentPage = pages.length - 1;

                renderChart();
                renderSummary(
                    document.getElementById("testMaxValue").textContent,
                    document.getElementById("testMaxTimestamp").textContent
                );
            }

            window.onload = function() {
                fetchData();
                setInterval(fetchData, 1000);
            };
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    init_csv()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)