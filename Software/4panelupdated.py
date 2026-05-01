import sys
import numpy as np
import serial
import time
from multiprocessing import Process, Array, Value, Event
import ctypes as ct
from scipy.interpolate import griddata
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

from PyQt5 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

# ── Config ───────────────────────────────────────────────────────────────
PORT = 'COM5'
BAUD = 250000
NUM_SENSORS = 5

# ── Sensor layout (meters) ───────────────────────────────────────────────
SENSOR_POS = np.array([
    [0.000, 0.018],   # ch0 top-left
    [0.018, 0.018],   # ch1 top-right
    [0.018, 0.000],   # ch2 bottom-right
    [0.009, 0.009],   # ch3 center
    [0.000, 0.000],   # ch4 bottom-left
])

# ── TMP102 physical positions (meters) — adjust to match your PCB ────────
TEMP_POS = np.array([
    [0.004, 0.009],   # T1 (ch5) — left side
    [0.014, 0.009],   # T2 (ch6) — right side
])

# ── Temperature safety thresholds (°C) ──────────────────────────────────
TEMP_SAFE    = 36.0
TEMP_CAUTION = 43.0
TEMP_DANGER  = 48.0

# ── Per-sensor chip orientation (radians, CCW from +X) ───────────────────
CHIP_ROTATIONS = np.zeros(NUM_SENSORS)

# ── Per-sensor circle+arrow panel layout (pixels in 220x220 canvas) ──────
CHIP_PX = np.array([
    [ 55,  55],   # ch0 top-left
    [165,  55],   # ch1 top-right
    [165, 165],   # ch2 bottom-right
    [110, 110],   # ch3 center
    [ 55, 165],   # ch4 bottom-left
], dtype=float)

CANVAS_SIZE = 220
BZ_SCALE    = 2.0
XY_SCALE    = 2.0

# ── Adaptive EMA baseline config ─────────────────────────────────────────
EMA_ALPHA          = 0.02
ACTIVITY_THRESHOLD = 3.0
DEAD_ZONE          = 5.0

# ── Heatmap / 3D grid ────────────────────────────────────────────────────
GRID_RES = 50
xi = np.linspace(0, 0.018, GRID_RES)
yi = np.linspace(0, 0.018, GRID_RES)
XI, YI = np.meshgrid(xi, yi)

HMAP_RES = 100
xi_h = np.linspace(0, 0.018, HMAP_RES)
yi_h = np.linspace(0, 0.018, HMAP_RES)
XI_H, YI_H = np.meshgrid(xi_h, yi_h)


# ── Parse ─────────────────────────────────────────────────────────────────
def parse_line(line):
    data  = np.zeros((NUM_SENSORS, 3))
    temps = [None, None]
    try:
        tokens = line.strip().split(',')
        i = 0
        while i < len(tokens):
            tok = tokens[i].strip()
            if tok.startswith('#'):
                ch = int(tok[1:])
                if ch < NUM_SENSORS and i + 3 < len(tokens):
                    data[ch] = [float(tokens[i+1]), float(tokens[i+2]), float(tokens[i+3])]
                i += 4
            elif tok == 'T1':
                temps[0] = float(tokens[i+1])
                i += 2
            elif tok == 'T2':
                temps[1] = float(tokens[i+1])
                i += 2
            else:
                i += 1
        return data, temps
    except Exception:
        return None, [None, None]


# ── Background serial reader process ─────────────────────────────────────
class SensorProcess(Process):
    def __init__(self):
        super().__init__(daemon=True)
        self._raw       = Array(ct.c_float, NUM_SENSORS * 3)
        self._baseline  = Array(ct.c_float, NUM_SENSORS * 3)
        self._counter   = Value(ct.c_uint64, 0)
        self._ready     = Event()
        self._recal_req = Event()
        self._temps     = Array(ct.c_float, 2)
        self._temps[0]  = -999.0
        self._temps[1]  = -999.0

        self.CONTACT_THRESHOLD = 3.5

    @property
    def counter(self):
        return self._counter.value

    @property
    def latest(self):
        raw      = np.array(self._raw[:]).reshape(NUM_SENSORS, 3)
        baseline = np.array(self._baseline[:]).reshape(NUM_SENSORS, 3)
        delta    = raw - baseline
        mean     = np.mean(delta, axis=0)
        delta    = delta - mean
        delta[np.abs(delta) < DEAD_ZONE] = 0.0
        return delta

    @property
    def temperatures(self):
        return list(self._temps)

    def request_recalibrate(self):
        self._recal_req.set()

    def wait_until_ready(self, timeout=20.0):
        return self._ready.wait(timeout=timeout)

    def _read_sample(self, ser):
        if ser.in_waiting > 4000:
            ser.reset_input_buffer()
            ser.readline()
        raw = ser.readline().decode('utf-8', errors='ignore')
        return parse_line(raw)

    def _hard_baseline(self, ser, n=30):
        print("[SensorProcess] Capturing baseline — keep sensor still...")
        samples = []
        while len(samples) < n:
            data, _ = self._read_sample(ser)
            if data is not None:
                samples.append(data)
        baseline = np.mean(samples, axis=0)
        self._baseline[:] = baseline.flatten().tolist()
        print("[SensorProcess] Baseline done.")
        return baseline.copy()

    def run(self):
        ser = serial.Serial(PORT, BAUD, timeout=1)
        print(f"[SensorProcess] Connected to {PORT}")
        time.sleep(2.0)
        ser.reset_input_buffer()

        baseline = self._hard_baseline(ser)
        self._ready.set()

        while True:
            if self._recal_req.is_set():
                self._recal_req.clear()
                baseline = self._hard_baseline(ser)

            data, temps = self._read_sample(ser)
            if data is None:
                continue

            self._raw[:] = data.flatten().tolist()
            self._counter.value += 1

            if temps[0] is not None:
                self._temps[0] = temps[0]
            if temps[1] is not None:
                self._temps[1] = temps[1]

            delta = data - baseline
            mean  = np.mean(delta, axis=0)
            delta_cm_removed  = delta - mean
            spatial_variation = np.std(delta_cm_removed, axis=0)
            variation_norm    = np.linalg.norm(spatial_variation)
            is_contact        = variation_norm > self.CONTACT_THRESHOLD

            if not is_contact:
                baseline = (1.0 - EMA_ALPHA) * baseline + EMA_ALPHA * data
                self._baseline[:] = baseline.flatten().tolist()


# ── Per-sensor circle+arrow widget ───────────────────────────────────────
class SensorDotWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._data = np.zeros((NUM_SENSORS, 3))

        self._rot = []
        for angle in CHIP_ROTATIONS:
            c, s = np.cos(angle), np.sin(angle)
            self._rot.append(np.array([[c, -s], [s, c]]))

    def update_data(self, data):
        self._data = data
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor('#1a1a2e'))

        side     = min(self.width(), self.height())
        x_offset = (self.width()  - side) / 2.0
        y_offset = (self.height() - side) / 2.0
        scale    = side / CANVAS_SIZE
        painter.translate(x_offset, y_offset)
        painter.scale(scale, scale)

        painter.setPen(QtGui.QPen(QtGui.QColor(80, 80, 120), 1))
        for px in CHIP_PX:
            painter.drawEllipse(int(px[0]) - 2, int(px[1]) - 2, 4, 4)

        for i, px in enumerate(CHIP_PX):
            bx, by, bz = self._data[i]
            radius = max(int(abs(bz) / BZ_SCALE), 1)
            cx, cy = int(px[0]), int(px[1])

            if bz >= 0:
                painter.setBrush(QtGui.QBrush(QtGui.QColor(220, 60, 60)))
                painter.setPen(QtGui.QPen(QtGui.QColor(220, 60, 60)))
            else:
                painter.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
                painter.setPen(QtGui.QPen(QtGui.QColor(220, 60, 60), 2))
            painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

            xy_raw = np.array([bx, -by])
            xy_rot = self._rot[i] @ xy_raw
            ax = cx + xy_rot[0] / XY_SCALE
            ay = cy + xy_rot[1] / XY_SCALE

            painter.setPen(QtGui.QPen(QtGui.QColor(46, 204, 113), 2))
            painter.drawLine(cx, cy, int(ax), int(ay))

            length = np.hypot(xy_rot[0], xy_rot[1]) / XY_SCALE
            if length > 3:
                angle = np.arctan2(xy_rot[1], xy_rot[0])
                head  = 6
                for side in [0.4, -0.4]:
                    hx = ax - head * np.cos(angle + side)
                    hy = ay - head * np.sin(angle + side)
                    painter.drawLine(int(ax), int(ay), int(hx), int(hy))

            painter.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200)))
            painter.drawText(cx + radius + 3, cy - radius, f"CH{i}")

        painter.end()


# ── 3D surface widget ─────────────────────────────────────────────────────
class MatplotlibSurfaceWidget(FigureCanvas):
    def __init__(self, parent=None):
        self.figure = Figure(facecolor='#1a1a2e')
        super().__init__(self.figure)
        if parent is not None:
            self.setParent(parent)

        self.setMinimumSize(300, 300)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        self.ax = self.figure.add_subplot(111, projection='3d')
        self.ax.set_facecolor('#1a1a2e')
        self.ax.view_init(elev=35, azim=-135)
        self.figure.subplots_adjust(left=0.02, right=0.98, bottom=0.05, top=0.95)

        self._surface       = None
        self._sensor_points = None
        self._configure_axes()
        self.update_surface(np.zeros(NUM_SENSORS))

    def _configure_axes(self):
        self.ax.set_title("3D Force Surface (Bz)", color='white', pad=12)
        self.ax.set_xlabel("X (mm)", color='white', labelpad=8)
        self.ax.set_ylabel("Y (mm)", color='white', labelpad=8)
        self.ax.set_zlabel("Bz (uT)", color='white', labelpad=6)
        self.ax.set_xlim(0, 18)
        self.ax.set_ylim(0, 18)
        self.ax.set_zlim(-100, 0)
        self.ax.tick_params(colors='white')
        self.ax.xaxis.pane.set_facecolor((0.10, 0.10, 0.18, 1.0))
        self.ax.yaxis.pane.set_facecolor((0.10, 0.10, 0.18, 1.0))
        self.ax.zaxis.pane.set_facecolor((0.10, 0.10, 0.18, 1.0))

    def update_surface(self, bz_sensors):
        zi = griddata(SENSOR_POS, bz_sensors, (XI_H, YI_H), method='cubic', fill_value=0)
        zi = np.clip(zi, 0, None)
        zi = -zi   # invert so touch pushes DOWN

        if self._surface is not None:
            self._surface.remove()
        if self._sensor_points is not None:
            self._sensor_points.remove()

        x_mm = XI_H * 1000.0
        y_mm = YI_H * 1000.0
        z_uT = zi
        vmax = max(float(np.max(np.abs(z_uT))), 1.0)

        self.ax.set_zlim(-max(vmax * 1.1, 10.0), 0)
        self._surface = self.ax.plot_surface(
            x_mm, y_mm, z_uT,
            cmap='turbo_r',
            linewidth=0,
            antialiased=True,
            vmin=-max(vmax, 10.0),
            vmax=0,
        )
        self._sensor_points = self.ax.scatter(
            SENSOR_POS[:, 0] * 1000.0,
            SENSOR_POS[:, 1] * 1000.0,
            -np.clip(bz_sensors, 0, None),
            c='white', s=18, depthshade=False,
        )
        self.draw_idle()


# ── Temperature heatmap widget ────────────────────────────────────────────
class TempHeatmapWidget(pg.GraphicsLayoutWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)

        self.plot = self.addPlot(title="Temperature Heatmap (°C)")
        self.plot.setLabel('left', 'Y (mm)')
        self.plot.setLabel('bottom', 'X (mm)')
        self.plot.setAspectLocked(True)

        self.img = pg.ImageItem()
        self.plot.addItem(self.img)
        self.img.setRect(pg.QtCore.QRectF(0, 0, 18, 18))

        # Blue (cold) → green (safe) → yellow (caution) → red (danger)
        # Mapped across 20–50 °C range
        colors = [
            (0,   80,  200),   # cold blue   ~20°C
            (0,   200, 100),   # safe green  ~36°C
            (255, 200, 0  ),   # caution     ~43°C
            (220, 40,  40 ),   # danger red  ~48°C+
        ]
        # Normalise threshold positions to [0,1] over 20–50°C
        TMIN, TMAX = 20.0, 50.0
        pos = np.array([
            0.0,
            (TEMP_SAFE    - TMIN) / (TMAX - TMIN),
            (TEMP_CAUTION - TMIN) / (TMAX - TMIN),
            1.0,
        ])
        cmap = pg.ColorMap(pos=pos, color=colors)
        self.colorbar = pg.ColorBarItem(
            values=(TMIN, TMAX), colorMap=cmap, label='°C'
        )
        self.colorbar.setImageItem(self.img, insert_in=self.plot)

        # Sensor position markers (triangles)
        temp_mm = TEMP_POS * 1000
        self.plot.plot(
            temp_mm[:, 0], temp_mm[:, 1],
            pen=None, symbol='t', symbolSize=12,
            symbolBrush='w', symbolPen='w'
        )

        # Readout labels for T1 and T2
        self._t1_label = pg.TextItem("T1: --.-°C", color='white', anchor=(0, 1))
        self._t2_label = pg.TextItem("T2: --.-°C", color='white', anchor=(1, 1))
        self._t1_label.setPos(0,   18)
        self._t2_label.setPos(18,  18)
        self.plot.addItem(self._t1_label)
        self.plot.addItem(self._t2_label)

        self._last_temps = [25.0, 25.0]
        self.TMIN = TMIN
        self.TMAX = TMAX

    def _temp_color(self, t):
        """Return a hex color string for a temperature value."""
        if t < TEMP_SAFE:
            return '#2ecc71'
        elif t < TEMP_CAUTION:
            return '#f39c12'
        else:
            return '#e74c3c'

    def update_temps(self, temps):
        t = [
            temps[i] if (temps[i] is not None and temps[i] > -900)
            else self._last_temps[i]
            for i in range(2)
        ]
        self._last_temps = t

        # Interpolate temperature across full 18×18 mm grid
        zi = griddata(TEMP_POS, np.array(t), (XI_H, YI_H),
                      method='linear', fill_value=float(np.mean(t)))

        # Normalise to [0, 1] for the colormap (20–50°C)
        zi_norm = (zi - self.TMIN) / (self.TMAX - self.TMIN)
        zi_norm = np.clip(zi_norm, 0, 1)

        self.img.setImage(zi_norm.T)
        self.colorbar.setLevels((self.TMIN, self.TMAX))

        # Update readout text with safety colour
        self._t1_label.setText(f"T1: {t[0]:.1f}°C")
        self._t2_label.setText(f"T2: {t[1]:.1f}°C")
        self._t1_label.setColor(self._temp_color(t[0]))
        self._t2_label.setColor(self._temp_color(t[1]))


# ── Main window ───────────────────────────────────────────────────────────
class Visualizer(QtWidgets.QMainWindow):
    def __init__(self, sensor: SensorProcess):
        super().__init__()
        self.sensor        = sensor
        self._last_counter = -1

        self.setWindowTitle("Tactile Sensor Visualizer  |  B = recalibrate baseline")
        self.resize(1200, 900)

        pg.setConfigOption('background', '#1a1a2e')
        pg.setConfigOption('foreground', 'w')

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QGridLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setRowStretch(0, 1)
        layout.setRowStretch(1, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

        # ── Panel 0,0: Bz Heatmap ────────────────────────────────────────
        self.heatmap_widget = pg.GraphicsLayoutWidget()
        layout.addWidget(self.heatmap_widget, 0, 0)

        self.heat_plot = self.heatmap_widget.addPlot(title="Bz Heatmap")
        self.heat_plot.setLabel('left', 'Y (mm)')
        self.heat_plot.setLabel('bottom', 'X (mm)')
        self.heat_plot.setAspectLocked(True)

        self.img_item = pg.ImageItem()
        self.heat_plot.addItem(self.img_item)
        self.img_item.setRect(pg.QtCore.QRectF(0, 0, 18, 18))

        colormap = pg.colormap.get('RdBu_r', source='matplotlib')
        self.colorbar = pg.ColorBarItem(values=(-100, 100), colorMap=colormap, label='Bz (uT)')
        self.colorbar.setImageItem(self.img_item, insert_in=self.heat_plot)

        sensor_mm = SENSOR_POS * 1000
        self.heat_plot.plot(
            sensor_mm[:, 0], sensor_mm[:, 1],
            pen=None, symbol='x', symbolSize=10,
            symbolBrush='w', symbolPen='w'
        )

        # ── Panel 0,1: Bar charts ─────────────────────────────────────────
        self.bar_widget = pg.GraphicsLayoutWidget()
        layout.addWidget(self.bar_widget, 0, 1)

        self.bar_plot = self.bar_widget.addPlot(title="Per-Sensor Field (uT)")
        self.bar_plot.setLabel('left', 'uT')
        self.bar_plot.setLabel('bottom', 'Sensor Channel')
        self.bar_plot.addLegend(offset=(10, 10))
        self.bar_plot.showGrid(y=True, alpha=0.3)

        x = np.arange(NUM_SENSORS)
        w = 0.25
        self.bars_bx = self._make_bars(x - w, w, '#e74c3c', 'Bx')
        self.bars_by = self._make_bars(x,     w, '#2ecc71', 'By')
        self.bars_bz = self._make_bars(x + w, w, '#3498db', 'Bz')

        ticks = [[(i, f'CH{i}') for i in range(NUM_SENSORS)]]
        self.bar_plot.getAxis('bottom').setTicks(ticks)
        self.bar_plot.setYRange(-200, 200)

        # ── Panel 1,0: Temperature heatmap ───────────────────────────────
        self.temp_widget = TempHeatmapWidget()
        layout.addWidget(self.temp_widget, 1, 0)

        # ── Panel 1,1: 3D surface ─────────────────────────────────────────
        surf_container = QtWidgets.QWidget()
        surf_layout    = QtWidgets.QVBoxLayout(surf_container)
        surf_layout.setContentsMargins(8, 8, 8, 8)
        surf_layout.setSpacing(6)

        surf_title = QtWidgets.QLabel("3D Force Surface (Bz)")
        surf_title.setAlignment(QtCore.Qt.AlignCenter)
        surf_title.setStyleSheet("color: white; font-size: 11px;")
        surf_layout.addWidget(surf_title)

        self.surface_widget = MatplotlibSurfaceWidget()
        surf_layout.addWidget(self.surface_widget)

        surf_legend = QtWidgets.QLabel(
            "turbo_r colormap = Bz intensity    white dots = sensor positions")
        surf_legend.setAlignment(QtCore.Qt.AlignCenter)
        surf_legend.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        surf_layout.addWidget(surf_legend)
        surf_layout.setStretch(1, 1)
        surf_container.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        layout.addWidget(surf_container, 1, 1)

        # ── Status bar ────────────────────────────────────────────────────
        self.statusBar().showMessage(
            "Ready  |  B = hard recalibrate  |  EMA adapts at rest  |  drag 3D panel to rotate")

        # ── Timer ─────────────────────────────────────────────────────────
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._update)
        self.timer.start(30)

    def _make_bars(self, x_positions, width, color, name):
        bars = []
        for i, xp in enumerate(x_positions):
            item = pg.BarGraphItem(
                x=[xp], height=[0], width=width, brush=color,
                name=name if i == 0 else None)
            self.bar_plot.addItem(item)
            bars.append(item)
        return bars

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_B:
            self.sensor.request_recalibrate()
            self.statusBar().showMessage("Recalibrating — keep sensor still...")
            QtCore.QTimer.singleShot(3000, lambda: self.statusBar().showMessage(
                "Baseline recaptured  |  EMA adapting  |  B = recalibrate again"))

    def _update(self):
        current = self.sensor.counter
        if current == self._last_counter:
            return
        self._last_counter = current

        data = self.sensor.latest   # (5,3) baseline-subtracted + dead-zoned

        # ── Bz heatmap ───────────────────────────────────────────────────
        bz   = data[:, 2]
        zi_h = griddata(SENSOR_POS, bz, (XI_H, YI_H), method='cubic', fill_value=0)
        self.img_item.setImage(zi_h.T)
        lim = max(float(np.abs(bz).max()), 1.0)
        self.colorbar.setLevels((-lim, lim))

        # ── Bar charts ────────────────────────────────────────────────────
        for i in range(NUM_SENSORS):
            self.bars_bx[i].setOpts(height=data[i, 0])
            self.bars_by[i].setOpts(height=data[i, 1])
            self.bars_bz[i].setOpts(height=data[i, 2])
        y_lim = max(float(np.abs(data).max()) * 1.2, 10.0)
        self.bar_plot.setYRange(-y_lim, y_lim)

        # ── 3D surface ────────────────────────────────────────────────────
        self.surface_widget.update_surface(bz)

        # ── Temperature heatmap ───────────────────────────────────────────
        self.temp_widget.update_temps(self.sensor.temperatures)

    def closeEvent(self, event):
        self.timer.stop()
        self.sensor.terminate()
        event.accept()


def main():
    sensor = SensorProcess()
    sensor.start()
    print("Waiting for sensor to be ready...")
    if not sensor.wait_until_ready(timeout=20.0):
        print("ERROR: Sensor did not become ready in time.")
        sensor.terminate()
        sys.exit(1)

    app = QtWidgets.QApplication(sys.argv)
    win = Visualizer(sensor)
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()