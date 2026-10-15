// Tracepaper's screen on a reMarkable 2: shows the view that the tablet's pen draws in.
//
// The viewer runs it on the tablet while connected, with the tablet's own app stopped
// (tracepaper/pen.py): tracepaper-screen IMAGE TURN PEN. IMAGE is a PNG of the whole
// screen as the tablet is held, turned TURN degrees clockwise from upright like the
// pen's coordinates; it is shown again whenever the file is replaced. Until the first
// image arrives, the screen says what it is for. A button in the corner, tapped twice
// with a finger, disconnects: the program signals the viewer's session that started it
// (SIGUSR2), which gives the tablet back to its own app.
//
// The viewer only sends a stroke once it is finished, so meanwhile this program draws
// the pen's strokes itself, read from the pen's input device PEN. Each picture comes
// with the time the pen touched down for the last stroke it contains (IMAGE.done, the
// input events' own time, which the viewer reads too); when it is shown, the strokes
// drawn here up to that one go, since the picture has them now.
//
// It draws with the tablet's own e-ink plugins for Qt Quick, started with
// QT_QPA_PLATFORM=epaper and QT_QUICK_BACKEND=epaper. While the pen draws, the screen
// is switched to the plugin's fast mode for pen strokes, as the tablet's own app does
// (see PenMode). Build it with build.sh.
//
// tracepaper-screen --receive FILE BYTES DONE reads exactly BYTES from its input into
// FILE and DONE into FILE.done, replacing FILE in one step; the viewer's session on the
// tablet uses it to take each picture out of its input, which the tablet's own tools
// can't do.
#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QPainter>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickPaintedItem>
#include <QQuickWindow>
#include <QSocketNotifier>
#include <QTimer>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <linux/input.h>
#include <signal.h>
#include <string>
#include <unistd.h>

// The pen sensor (SENSOR_X runs up the upright screen, SENSOR_Y across it) and the
// screen it covers, upright, in pixels; as in tracepaper/pen.py.
static const double SENSOR_X = 20967, SENSOR_Y = 15725, SCREEN_W = 1404, SCREEN_H = 1872;
static const double INK_WIDTH = 3.5; // pixels, about what the viewer's ink comes out as

// Raw reads, so that nothing after the picture is taken from the input.
static int receive(const char *path, long bytes, const char *done) {
    const std::string part = std::string(path) + ".new";
    FILE *out = fopen(part.c_str(), "wb");
    if (!out) {
        perror(part.c_str());
        return 1;
    }
    char buffer[65536];
    while (bytes > 0) {
        const ssize_t n = read(0, buffer, bytes < (long)sizeof buffer ? bytes : sizeof buffer);
        if (n <= 0) {
            fclose(out);
            remove(part.c_str());
            return 1;
        }
        fwrite(buffer, 1, n, out);
        bytes -= n;
    }
    FILE *mark = fopen((std::string(path) + ".done").c_str(), "w");
    if (mark) {
        fputs(done, mark);
        fclose(mark);
    }
    if (fclose(out) != 0 || rename(part.c_str(), path) != 0) {
        perror(path);
        return 1;
    }
    return 0;
}

// The e-ink plugin updates the screen with the mode an EPScreenModeItem sets for its area:
// "Pen" is fast and black and white, for strokes, "Content" slower and in greys, for
// pictures. The plugin doesn't publish that class, so it is looked up at run time; if it
// isn't there (another software version), the screen keeps its usual mode.
class PenMode {
public:
    explicit PenMode(QQuickItem *screen) {
        void *lib = dlopen("/usr/lib/plugins/scenegraph/libqsgepaper.so", RTLD_NOW | RTLD_NOLOAD);
        if (!lib)
            return;
        auto construct = reinterpret_cast<void (*)(void *, QQuickItem *)>(
            dlsym(lib, "_ZN16EPScreenModeItemC1EP10QQuickItem"));
        auto *meta = static_cast<const QMetaObject *>(dlsym(lib, "_ZN16EPScreenModeItem16staticMetaObjectE"));
        const int index = meta ? meta->indexOfEnumerator("Mode") : -1;
        if (!construct || index < 0 || meta->indexOfProperty("mode") < 0)
            return;
        pen = meta->enumerator(index).keyToValue("Pen");
        content = meta->enumerator(index).keyToValue("Content");
        if (pen < 0 || content < 0)
            return;
        // Its size isn't published either; a QQuickItem keeps its state elsewhere, so this is ample.
        void *memory = ::operator new(4096);
        memset(memory, 0, 4096);
        construct(memory, screen);
        item = static_cast<QQuickItem *>(memory);
        item->setSize(screen->size());
        set(content);
    }
    bool found() const { return item; }
    void drawing() { set(pen); }
    void picture() { set(content); }

private:
    QQuickItem *item = nullptr;
    int pen = -1, content = -1, mode = -1;
    void set(int m) {
        if (item && m != mode) {
            mode = m;
            item->setProperty("mode", m);
        }
    }
};

// The strokes drawn since the last picture that has them, over the whole screen, upright.
class LiveInk : public QQuickPaintedItem {
public:
    LiveInk(const QString &device, PenMode *mode, QQuickItem *parent) : QQuickPaintedItem(parent), mode(mode) {
        const int fd = open(device.toLocal8Bit().constData(), O_RDONLY | O_NONBLOCK);
        if (fd < 0) {
            perror(device.toLocal8Bit().constData());
            return;
        }
        auto *notifier = new QSocketNotifier(fd, QSocketNotifier::Read, this);
        connect(notifier, &QSocketNotifier::activated, this, [this, fd] { readPen(fd); });
    }

    // A picture with the strokes up to the one that touched down at done is shown.
    void shownUpTo(qint64 done) {
        const qsizetype before = strokes.size();
        strokes.removeIf([done](const Stroke &s) { return s.down <= done; });
        if (strokes.size() != before)
            update();
    }

    void paint(QPainter *painter) override { // pure black, which the fast mode shows best
        painter->setPen(QPen(Qt::black, INK_WIDTH, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
        for (const Stroke &s : strokes) {
            if (s.points.size() == 1)
                painter->drawPoint(s.points.first());
            else
                painter->drawPolyline(s.points);
        }
    }

private:
    struct Stroke {
        qint64 down; // when the pen touched down, in microseconds of the input events' clock
        QPolygonF points;
    };
    QList<Stroke> strokes;
    PenMode *mode;
    int x = 0, y = 0;
    bool tip = false, eraser = false, touching = false, starting = false;
    qint64 downAt = 0;

    void readPen(int fd) {
        input_event events[64];
        ssize_t n;
        while ((n = read(fd, events, sizeof events)) > 0) {
            for (const input_event &e : QSpan(events, n / sizeof(input_event)))
                handle(e);
        }
    }

    void handle(const input_event &e) {
        if (e.type == EV_ABS && e.code == ABS_X) {
            x = e.value;
        } else if (e.type == EV_ABS && e.code == ABS_Y) {
            y = e.value;
        } else if (e.type == EV_KEY && e.code == BTN_TOOL_PEN) {
            tip = e.value;
        } else if (e.type == EV_KEY && e.code == BTN_TOOL_RUBBER) {
            eraser = e.value;
        } else if (e.type == EV_KEY && e.code == BTN_TOUCH) {
            touching = e.value;
            if (touching) {
                downAt = qint64(e.input_event_sec) * 1000000 + e.input_event_usec;
                starting = true;
            }
        } else if (e.type == EV_SYN && e.code == SYN_REPORT && touching && tip && !eraser) {
            const QPointF p(y * SCREEN_W / SENSOR_Y, SCREEN_H - x * SCREEN_H / SENSOR_X);
            QRectF changed(p, p);
            if (starting) {
                strokes.append({downAt, QPolygonF{p}});
                starting = false;
            } else {
                changed = QRectF(strokes.last().points.last(), p).normalized();
                strokes.last().points.append(p);
            }
            mode->drawing();
            update(changed.adjusted(-INK_WIDTH, -INK_WIDTH, INK_WIDTH, INK_WIDTH).toAlignedRect());
        }
    }
};

static const char *QML = R"(
import QtQuick
Window {
    id: win
    visible: true
    width: Screen.width
    height: Screen.height
    color: "white"
    property int generation: 0
    property bool leaving: false
    Item {
        anchors.centerIn: parent
        width: turn % 180 ? parent.height : parent.width
        height: turn % 180 ? parent.width : parent.height
        rotation: -turn
        Image {
            id: view
            anchors.fill: parent
            cache: false
            source: win.generation ? imageUrl + "?" + win.generation : ""
        }
        Text {
            anchors.centerIn: parent
            visible: view.status !== Image.Ready
            horizontalAlignment: Text.AlignHCenter
            font.pixelSize: 40
            text: "Tracepaper\n\nThe view that the pen draws in appears here."
        }
        // Fingers only reach this program; the pen draws, on the computer too.
        Rectangle {
            id: leave
            property bool asking: false
            anchors { top: parent.top; right: parent.right; margins: 16 }
            width: label.implicitWidth + 48
            height: 72
            radius: 12
            color: "white"
            border { color: "black"; width: 2 }
            Text {
                id: label
                anchors.centerIn: parent
                font.pixelSize: 32
                text: leave.asking ? "Tap again to disconnect" : "Disconnect"
            }
            MouseArea {
                anchors.fill: parent
                onClicked: {
                    if (leave.asking) win.leaving = true
                    leave.asking = !leave.asking
                    if (leave.asking) ask.restart()
                }
            }
            Timer { id: ask; interval: 4000; onTriggered: leave.asking = false }
        }
    }
}
)";

int main(int argc, char *argv[]) {
    if (argc == 5 && strcmp(argv[1], "--receive") == 0)
        return receive(argv[2], atol(argv[3]), argv[4]);
    QGuiApplication app(argc, argv);
    const QStringList args = app.arguments();
    if (args.size() != 4) {
        fprintf(stderr, "usage: tracepaper-screen IMAGE TURN PEN\n"
                        "       tracepaper-screen --receive FILE BYTES DONE\n");
        return 2;
    }
    const QString path = args.at(1);
    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty("imageUrl", QUrl::fromLocalFile(path).toString());
    engine.rootContext()->setContextProperty("turn", args.at(2).toInt());
    engine.loadData(QML);
    if (engine.rootObjects().isEmpty())
        return 1;
    auto *window = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    window->contentItem()->setSize(QSizeF(SCREEN_W, SCREEN_H));
    PenMode mode(window->contentItem());
    fprintf(stderr, "fast pen mode: %s\n", mode.found() ? "found" : "not found");
    auto *ink = new LiveInk(args.at(3), &mode, window->contentItem());
    ink->setSize(QSizeF(SCREEN_W, SCREEN_H));
    ink->setZ(1);
    // The viewer replaces the file in one step (write, then rename), so it is never half written.
    QPair<QDateTime, qint64> shown;
    QTimer timer;
    QObject::connect(&timer, &QTimer::timeout, [&] {
        if (window->property("leaving").toBool()) {
            window->setProperty("leaving", false);
            kill(getppid(), SIGUSR2);
        }
        const QFileInfo file(path);
        const QPair<QDateTime, qint64> now(file.lastModified(), file.size());
        if (!file.exists() || now == shown)
            return;
        shown = now;
        QFile done(path + ".done");
        const qint64 upTo = done.open(QIODevice::ReadOnly) ? done.readAll().trimmed().toLongLong() : 0;
        mode.picture();
        window->setProperty("generation", window->property("generation").toInt() + 1);
        ink->shownUpTo(upTo);
    });
    timer.start(200);
    return app.exec();
}
