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

# ── Per-sensor chip orientation (radians, CCW from +X) ───────────────────
# All 5 chips: pin-1 dot at top-left → Bx points right, By points down → 0 rad
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
BZ_SCALE    = 2.0   # uT -> px radius
XY_SCALE    = 2.0   # uT -> px arrow length

# ── Adaptive EMA baseline config ─────────────────────────────────────────
# At 10Hz with alpha=0.02, time constant ~= 1/(0.02*10) = 5 seconds.
EMA_ALPHA          = 0.02
ACTIVITY_THRESHOLD = 3.0   # uT — freeze EMA during contact
DEAD_ZONE          = 5.0   # uT — clamp noise to zero before display

# ── Heatmap / 3D grid ────────────────────────────────────────────────────
GRID_RES = 50   # resolution of interpolated grid (20x20 is smooth enough for GL)
xi = np.linspace(0, 0.018, GRID_RES)
yi = np.linspace(0, 0.018, GRID_RES)
XI, YI = np.meshgrid(xi, yi)

# 2D heatmap uses higher resolution
HMAP_RES = 100
xi_h = np.linspace(0, 0.018, HMAP_RES)
yi_h = np.linspace(0, 0.018, HMAP_RES)
XI_H, YI_H = np.meshgrid(xi_h, yi_h)


# ── Parse ─────────────────────────────────────────────────────────────────
def parse_line(line):
    data = np.zeros((NUM_SENSORS, 3))
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
            else:
                i += 1
        return data
    except Exception:
        return None


# ── Background serial reader process ─────────────────────────────────────
class SensorProcess(Process):
    def __init__(self):
        super().__init__(daemon=True)
        self._raw       = Array(ct.c_float, NUM_SENSORS * 3)
        self._baseline  = Array(ct.c_float, NUM_SENSORS * 3)
        self._counter   = Value(ct.c_uint64, 0)
        self._ready     = Event()
        self._recal_req = Event()

        # NEW: contact detection tuning
        self.CONTACT_THRESHOLD = 3.5   # uT (tune this)  2.5 initial

    @property
    def counter(self):
        return self._counter.value

    @property
    def latest(self):
        raw      = np.array(self._raw[:]).reshape(NUM_SENSORS, 3)
        baseline = np.array(self._baseline[:]).reshape(NUM_SENSORS, 3)

        # Step 1: baseline subtraction
        delta = raw - baseline

        # Step 2: REMOVE rigid-body motion 
        mean = np.mean(delta, axis=0)
        delta = delta - mean

        # Step 3: dead zone
        delta[np.abs(delta) < DEAD_ZONE] = 0.0

        return delta

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
            data = self._read_sample(ser)
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

            data = self._read_sample(ser)
            if data is None:
                continue

            # Save raw
            self._raw[:] = data.flatten().tolist()
            self._counter.value += 1

            # ───────────── NEW LOGIC STARTS HERE ─────────────

            delta = data - baseline

            # Remove rigid-body motion
            mean = np.mean(delta, axis=0)
            delta_cm_removed = delta - mean

            # Compute spatial variation (KEY SIGNAL)
            spatial_variation = np.std(delta_cm_removed, axis=0)
            variation_norm = np.linalg.norm(spatial_variation)

            # CONTACT DETECTION
            is_contact = variation_norm > self.CONTACT_THRESHOLD

            # BASELINE UPDATE ONLY WHEN NO CONTACT
            if not is_contact:
                baseline = (1.0 - EMA_ALPHA) * baseline + EMA_ALPHA * data
                self._baseline[:] = baseline.flatten().tolist()


# ── Colormap: g y r
def bz_to_rgba(zi, vmax=30.0):
    """
    Map a 2D Bz grid to RGBA colors.
    0 uT = dark green, vmax uT = red. Negative values = blue tint.
    Returns float32 array of shape (GRID_RES, GRID_RES, 4).
    """
    t = np.clip(zi / vmax, -1.0, 1.0)
    rgba = np.zeros((*zi.shape, 4), dtype=np.float32)

    pos = np.clip(t, 0, 1)   # 0→1 for press
    neg = np.clip(-t, 0, 1)  # 0→1 for lift

    # Press: green(0) → yellow(0.5) → red(1)
    rgba[..., 0] = pos                          # R: rises with force
    rgba[..., 1] = 1.0 - pos * 0.8             # G: drops with force
    rgba[..., 2] = neg * 0.6                    # B: slight blue for negative
    rgba[..., 3] = 0.85                         # alpha

    return rgba


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

        side = min(self.width(), self.height())
        x_offset = (self.width() - side) / 2.0
        y_offset = (self.height() - side) / 2.0
        scale = side / CANVAS_SIZE
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
                head = 6
                for side in [0.4, -0.4]:
                    hx = ax - head * np.cos(angle + side)
                    hy = ay - head * np.sin(angle + side)
                    painter.drawLine(int(ax), int(ay), int(hx), int(hy))

            painter.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200)))
            painter.drawText(cx + radius + 3, cy - radius, f"CH{i}")

        painter.end()


# ── 3D surface widget ─────────────────────────────────────────────────────
class MatplotlibSurfaceWidget(FigureCanvas):
    """Embedded Matplotlib 3D surface with colormap shading."""
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

        self._surface = None
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
        self.ax.set_zlim(-100, 0)          # ← resting state at top (0), press goes negative
        self.ax.tick_params(colors='white')
        self.ax.xaxis.pane.set_facecolor((0.10, 0.10, 0.18, 1.0))
        self.ax.yaxis.pane.set_facecolor((0.10, 0.10, 0.18, 1.0))
        self.ax.zaxis.pane.set_facecolor((0.10, 0.10, 0.18, 1.0))

    def update_surface(self, bz_sensors):
        zi = griddata(SENSOR_POS, bz_sensors, (XI_H, YI_H), method='cubic', fill_value=0)
        zi = np.clip(zi, 0, None)
        zi = -zi                           # ← invert so touch pushes DOWN

        if self._surface is not None:
            self._surface.remove()
        if self._sensor_points is not None:
            self._sensor_points.remove()

        x_mm = XI_H * 1000.0
        y_mm = YI_H * 1000.0
        z_uT = zi
        vmax = max(float(np.max(np.abs(z_uT))), 1.0)

        self.ax.set_zlim(-max(vmax * 1.1, 10.0), 0)   # ← floor is negative, ceiling is 0
        self._surface = self.ax.plot_surface(
            x_mm,
            y_mm,
            z_uT,
            cmap='turbo_r',
            linewidth=0,
            antialiased=True,
            vmin=-max(vmax, 10.0),         # ← flipped colormap range
            vmax=0,
        )
        self._sensor_points = self.ax.scatter(
            SENSOR_POS[:, 0] * 1000.0,
            SENSOR_POS[:, 1] * 1000.0,
            -np.clip(bz_sensors, 0, None), # ← sensor dots also inverted
            c='white',
            s=18,
            depthshade=False,
        )
        self.draw_idle()

class Visualizer(QtWidgets.QMainWindow):
    def __init__(self, sensor: SensorProcess):
        super().__init__()
        self.sensor = sensor
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

        # ── Panel 1: Bz Heatmap ──────────────────────────────────────────
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

        # ── Panel 2: Bar charts ───────────────────────────────────────────
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

        # ── Panel 3: Circle + arrow ───────────────────────────────────────
        dot_container = QtWidgets.QWidget()
        dot_layout = QtWidgets.QVBoxLayout(dot_container)
        dot_layout.setContentsMargins(8, 8, 8, 8)
        dot_layout.setSpacing(6)

        lbl = QtWidgets.QLabel("Bz (circle) + Bx/By (arrow)")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("color: white; font-size: 11px;")
        dot_layout.addWidget(lbl)

        self.dot_widget = SensorDotWidget()
        dot_layout.addWidget(self.dot_widget)

        legend = QtWidgets.QLabel("  filled = press    outline = lift    arrow = shear")
        legend.setAlignment(QtCore.Qt.AlignCenter)
        legend.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        dot_layout.addWidget(legend)
        dot_layout.setStretch(1, 1)
        dot_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        layout.addWidget(dot_container, 1, 0)

        # ── Panel 4: 3D surface ───────────────────────────────────────────
        surf_container = QtWidgets.QWidget()
        surf_layout = QtWidgets.QVBoxLayout(surf_container)
        surf_layout.setContentsMargins(8, 8, 8, 8)
        surf_layout.setSpacing(6)

        surf_title = QtWidgets.QLabel("3D Force Surface (Bz)")
        surf_title.setAlignment(QtCore.Qt.AlignCenter)
        surf_title.setStyleSheet("color: white; font-size: 11px;")
        surf_layout.addWidget(surf_title)

        self.surface_widget = MatplotlibSurfaceWidget()
        surf_layout.addWidget(self.surface_widget)

        surf_legend = QtWidgets.QLabel("turbo colormap = Bz intensity    white dots = sensor positions")
        surf_legend.setAlignment(QtCore.Qt.AlignCenter)
        surf_legend.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        surf_layout.addWidget(surf_legend)
        surf_layout.setStretch(1, 1)
        surf_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        layout.addWidget(surf_container, 1, 1)

        # ── Status bar ───────────────────────────────────────────────────
        self.statusBar().showMessage(
            "Ready  |  B = hard recalibrate  |  EMA adapts at rest  |  drag 3D panel to rotate")

        # ── Timer ────────────────────────────────────────────────────────
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._update)
        self.timer.start(30)

    def _make_bars(self, x_positions, width, color, name):
        bars = []
        for i, xp in enumerate(x_positions):
            item = pg.BarGraphItem(x=[xp], height=[0], width=width, brush=color,
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

        # Heatmap
        bz = data[:, 2]
        zi_h = griddata(SENSOR_POS, bz, (XI_H, YI_H), method='cubic', fill_value=0)
        self.img_item.setImage(zi_h.T)
        lim = max(float(np.abs(bz).max()), 1.0)
        self.colorbar.setLevels((-lim, lim))

        # Bars
        for i in range(NUM_SENSORS):
            self.bars_bx[i].setOpts(height=data[i, 0])
            self.bars_by[i].setOpts(height=data[i, 1])
            self.bars_bz[i].setOpts(height=data[i, 2])
        y_lim = max(float(np.abs(data).max()) * 1.2, 10.0)
        self.bar_plot.setYRange(-y_lim, y_lim)

        # Dot panel
        self.dot_widget.update_data(data)

        # 3D surface
        self.surface_widget.update_surface(bz)

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

